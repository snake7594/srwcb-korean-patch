#!/usr/bin/env python3
"""Repack SECOND.WAR's Korean menu resources inside the retail image.

SECOND uses fixed work buffers throughout the RAM that follows its BSS.  A
previous implementation extended the PS-X EXE into that address space, which
let normal map/menu rendering overwrite translated strings.  This module
keeps the retail executable size and memory map, reuses only guarded original
record spans, and rewrites the proven self-relative pointer fields.

The patcher is intentionally fail-closed.  Every source record is checked by
SHA-256 (and, when supplied, exact bytes), replacement spans may cover glyph
bytes only, renderer controls must remain unchanged, and no text is shortened
or truncated to fit an old slot.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator


PSX_EXE_MAGIC = b"PS-X EXE"
PSX_HEADER_BYTES = 0x800
PSX_FILE_TO_RAM_BIAS = 0x8000F800
RETAIL_FILE_BYTES = 0x12B000
RETAIL_T_SIZE = 0x12A800

# The 91-record preview/conditions pool is the tail of root VM script 3.  It
# has no leaf pointers, so the complete script is copied to the arena and the
# sole authoritative root-table field is redirected.  The original block is
# deliberately retained for any non-authoritative incidental readers.
ROOT_RESOURCE_HEADER = 0xE14
ROOT_SCRIPT_ENTRY3_FIELD = 0xE24
ROOT_SCRIPT_ENTRY3_START = 0x1231
ROOT_SCRIPT_ENTRY3_END = 0x6A6C
ROOT_SCRIPT_ENTRY3_SHA256 = "9a339054071a07842f98462be5b62e9189f9b3e8b13c09b958627fe3624e1ca5"

# Moving the four-entry root header and its three shared prefix entry points
# frees 0xE14..0x1230.  Script 3 can then grow backwards while still ending
# before the untouched following resource at 0x6A6C.  The destination is 33
# consecutive font slots (0xA47..0xA67) that are absent from every final UI
# record; the builder additionally rejects a glyph map that assigns them.
ROOT_RELOCATION_CAVE_START = 0x3C938
ROOT_RELOCATION_CAVE_END = 0x3CD58
ROOT_PREFIX_BLOCK_START = ROOT_RESOURCE_HEADER
ROOT_PREFIX_BLOCK_END = ROOT_SCRIPT_ENTRY3_START
ROOT_PREFIX_BLOCK_SHA256 = "f51f9590971f0b1a92c673c7a795b40003d44f8f2693e6ed2e0172fe326a46ef"
ROOT_RELOCATION_CAVE_SHA256 = "da37de2fb3c1e6bf07d2323f448fc9e578f0e9aba2e699fe734e66de546cbadd"
ROOT_HEADER_LOAD_SITES = (
    (0x48160, 4),  # a0
    (0x48184, 4),  # a0
    (0x59928, 2),  # v0
    (0x59FC0, 2),  # v0
)

# The final dynamic font currently assigns glyphs only through 0xA2F.  Keep
# the root-header cave above separate, then use the still-unassigned 0xA68..
# 0xAFF tail as guarded static storage.  This is deliberately conditional on
# both the pristine bytes and the final glyph map: a future translation that
# needs one of these slots must fail instead of silently corrupting its font.
STATIC_FONT_DONOR_GLYPH_START = 0xA68
STATIC_FONT_DONOR_GLYPH_END = 0xB00
STATIC_FONT_DONOR_START = 0x3CD58
STATIC_FONT_DONOR_END = 0x3E058
STATIC_FONT_DONOR_SHA256 = "d5993c29f25d93133c3f4e2a3b65a7a727282f6abc5d3ebc8538fa550a0b44c1"
# One final preview record retains source glyph 0xAA7 outside its translated
# span.  Two preserved, unreferenced BMESS2 records also contain 0xAFA/0xAFB.
# Leave all three pixels intact; the latter two are not live runtime text, but
# retaining them keeps the cross-archive font-tail claim conservative.
STATIC_FONT_DONOR_EXCLUDED_GLYPHS = (0xAA7, 0xAFA, 0xAFB)

# Common master entry 23 stores two fixed-width audio labels.  Korean
# "모노" needs four encoded bytes while "스테레오" needs eight, so both
# selector call sites are widened from 4 to 8 bytes and the first label is
# space-padded to the same width.
COMMON_AUDIO_OPTION_WIDTH_PATCHES = (
    (0x8FEE4, bytes.fromhex("04 00 06 24"), bytes.fromhex("08 00 06 24")),
    (0xA1040, bytes.fromhex("04 00 06 24"), bytes.fromhex("08 00 06 24")),
)

SECOND_EXECUTABLE = Path("SECOND/SECOND.WAR")

CONTROL_ARG_LENGTHS: dict[int, int] = {
    0xF6: 0,
    0xF7: 0,
    0xF8: 1,
    0xF9: 1,
    0xFA: 0,
    0xFB: 2,
    0xFC: 2,
    0xFD: 2,
    0xFE: 1,
}

# SECOND's pointer-backed UI master is a stateful VM stream, not an ordinary
# dialogue-renderer string.  F7 owns two argument bytes here and switches the
# following stream to extended-font mode when bit 0x40 is set in its big-endian
# argument word.  Preview/dialogue records keep CONTROL_ARG_LENGTHS above,
# where F7 remains a zero-argument page break.
SECOND_UI_VM_COMMON_ARG_LENGTHS: dict[int, int] = {
    0xF6: 0,
    0xF7: 2,
    0xF8: 1,
    0xF9: 1,
    0xFA: 0,
    0xFB: 2,
    0xFC: 2,
    0xFD: 2,
    0xFE: 1,
}

SECOND_UI_VM_COMPACT_ARG_LENGTHS: dict[int, int] = {
    0xF0: 3,
    0xF1: 2,
    0xF2: 3,
    0xF3: 3,
    0xF4: 1,
    0xF5: 3,
}

RENDERER_GRAMMAR = "renderer"
SECOND_UI_VM_GRAMMAR = "second_ui_vm"

JAPANESE_OR_HAN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")

CHAR_NORMALISATION = {
    "\u00a0": " ",
    "\u3000": " ",
    "\t": " ",
    "，": ",",
    "．": ".",
    "？": "?",
    "！": "!",
    "：": ":",
    "；": ";",
    "（": "(",
    "）": ")",
    "［": "[",
    "］": "]",
    "【": "[",
    "】": "]",
    "‘": "'",
    "’": "'",
    "·": "・",
    "ㆍ": "・",
    "~": "〜",
    "～": "〜",
    "—": "-",
    "–": "-",
    "―": "-",
    "−": "-",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def s32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("alignment must be a positive power of two")
    return (value + alignment - 1) & -alignment


def encode_glyph_index(index: int) -> bytes:
    if not 0 <= index < 0xB00:
        raise ValueError(f"glyph index outside font: 0x{index:X}")
    if index < 0xEB:
        return bytes((index,))
    return bytes((0xEB + (index >> 8), index & 0xFF))


def normalise_ui_text(text: str) -> str:
    return "".join(CHAR_NORMALISATION.get(char, char) for char in text)


def encode_ui_text(text: str, glyph_map: dict[str, int], *, terminate: bool) -> bytes:
    """Encode UI text without dialogue wrapping; newlines become renderer F6."""

    text = normalise_ui_text(text)
    # U+30FB is punctuation retained in the safe low font; it is not Japanese
    # language content for this audit.
    bad = sorted(
        {
            char
            for char in text
            if JAPANESE_OR_HAN_RE.fullmatch(char) and char != "・"
        }
    )
    if bad:
        rendered = " ".join(f"{char}(U+{ord(char):04X})" for char in bad)
        raise ValueError(f"Japanese/Han remains in Korean UI text: {rendered}")
    output = bytearray()
    for char in text:
        if char == "\r":
            continue
        if char == "\n":
            output.append(0xF6)
            continue
        try:
            index = glyph_map[char]
        except KeyError as exc:
            raise ValueError(
                f"no allocated font glyph for UI character {char!r} U+{ord(char):04X}"
            ) from exc
        output.extend(encode_glyph_index(index))
    if terminate:
        output.append(0xFF)
    return bytes(output)


@dataclass(frozen=True)
class RendererToken:
    start: int
    end: int
    kind: str
    raw: bytes


def parse_renderer_record(data: bytes | bytearray, start: int, limit: int | None = None) -> tuple[int, list[RendererToken]]:
    if limit is None:
        limit = len(data)
    cursor = start
    tokens: list[RendererToken] = []
    while cursor < limit:
        token_start = cursor
        opcode = data[cursor]
        if opcode < 0xEB:
            cursor += 1
            tokens.append(RendererToken(token_start, cursor, "glyph", bytes(data[token_start:cursor])))
            continue
        if opcode < 0xF6:
            if cursor + 2 > limit:
                raise ValueError(f"truncated two-byte glyph at 0x{cursor:X}")
            cursor += 2
            tokens.append(RendererToken(token_start, cursor, "glyph", bytes(data[token_start:cursor])))
            continue
        if opcode == 0xFF:
            cursor += 1
            tokens.append(RendererToken(token_start, cursor, "terminator", b"\xFF"))
            return cursor, tokens
        arg_bytes = CONTROL_ARG_LENGTHS.get(opcode)
        if arg_bytes is None:
            raise ValueError(f"unknown renderer opcode 0x{opcode:02X} at 0x{cursor:X}")
        cursor += 1 + arg_bytes
        if cursor > limit:
            raise ValueError(f"truncated renderer control at 0x{token_start:X}")
        tokens.append(RendererToken(token_start, cursor, "control", bytes(data[token_start:cursor])))
    raise ValueError(f"unterminated renderer record at 0x{start:X}")


def parse_second_ui_vm_record(
    data: bytes | bytearray,
    start: int,
    limit: int | None = None,
) -> tuple[int, list[RendererToken]]:
    """Parse one stateful record from SECOND's pointer-backed UI master.

    The initial mode is deliberately unknown.  Every retail master record
    executes F7 before its first mode-sensitive EB..F5 byte, so assuming a
    stale runtime mode is unnecessary.  A malformed/new record that violates
    that invariant fails closed instead of silently choosing a token width.
    """

    if limit is None:
        limit = len(data)
    cursor = start
    mode: str | None = None
    tokens: list[RendererToken] = []
    while cursor < limit:
        token_start = cursor
        opcode = data[cursor]

        if opcode < 0xEB:
            cursor += 1
            kind = "glyph" if mode == "extended" else "compact_data"
            tokens.append(
                RendererToken(token_start, cursor, kind, bytes(data[token_start:cursor]))
            )
            continue

        if mode == "extended" and opcode < 0xF6:
            if cursor + 2 > limit:
                raise ValueError(f"truncated SECOND UI two-byte glyph at 0x{cursor:X}")
            cursor += 2
            tokens.append(
                RendererToken(token_start, cursor, "glyph", bytes(data[token_start:cursor]))
            )
            continue

        if mode == "compact" and opcode < 0xF0:
            cursor += 1
            tokens.append(
                RendererToken(
                    token_start,
                    cursor,
                    "compact_data",
                    bytes(data[token_start:cursor]),
                )
            )
            continue

        if opcode == 0xFF:
            cursor += 1
            tokens.append(RendererToken(token_start, cursor, "terminator", b"\xFF"))
            return cursor, tokens

        arg_bytes = SECOND_UI_VM_COMMON_ARG_LENGTHS.get(opcode)
        if arg_bytes is None and mode == "compact":
            arg_bytes = SECOND_UI_VM_COMPACT_ARG_LENGTHS.get(opcode)
        if arg_bytes is None:
            if mode is None and 0xEB <= opcode <= 0xF5:
                raise ValueError(
                    f"SECOND UI mode-sensitive byte 0x{opcode:02X} before F7 "
                    f"at 0x{cursor:X}"
                )
            raise ValueError(
                f"unknown SECOND UI opcode 0x{opcode:02X} at 0x{cursor:X} "
                f"in {mode or 'unknown'} mode"
            )

        cursor += 1 + arg_bytes
        if cursor > limit:
            raise ValueError(f"truncated SECOND UI control at 0x{token_start:X}")
        raw = bytes(data[token_start:cursor])
        tokens.append(RendererToken(token_start, cursor, "control", raw))
        if opcode == 0xF7:
            argument_word = (raw[1] << 8) | raw[2]
            mode = "extended" if argument_word & 0x40 else "compact"

    raise ValueError(f"unterminated SECOND UI record at 0x{start:X}")


def _parse_record(
    data: bytes | bytearray,
    start: int,
    limit: int | None,
    grammar: str,
) -> tuple[int, list[RendererToken]]:
    if grammar == RENDERER_GRAMMAR:
        return parse_renderer_record(data, start, limit)
    if grammar == SECOND_UI_VM_GRAMMAR:
        return parse_second_ui_vm_record(data, start, limit)
    raise ValueError(f"unknown UI record grammar {grammar!r}")


def record_bytes(
    data: bytes | bytearray,
    start: int,
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> bytes:
    end, _tokens = _parse_record(data, start, None, grammar)
    return bytes(data[start:end])


def control_signature(
    raw: bytes,
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> tuple[str, ...]:
    end, tokens = _parse_record(raw, 0, len(raw), grammar)
    if end != len(raw):
        raise ValueError("bytes follow renderer record terminator")
    return tuple(token.raw.hex(" ").upper() for token in tokens if token.kind == "control")


def _int(value: Any, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"{field} must be an integer, got {value!r}")


def _verify_record_guard(
    executable: bytes | bytearray,
    row: dict[str, Any],
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> bytes:
    start = _int(row.get("source_offset", row.get("target")), "source_offset")
    expected_hex = row.get("raw_hex") or row.get("source_hex")
    if expected_hex:
        expected = bytes.fromhex(str(expected_hex))
        raw = bytes(executable[start:start + len(expected)])
        if raw != expected:
            raise ValueError(f"source bytes changed for record at 0x{start:X}")
    elif row.get("end_offset_exclusive") is not None:
        end = _int(row["end_offset_exclusive"], "end_offset_exclusive")
        if not start < end <= len(executable):
            raise ValueError(f"invalid guarded record span 0x{start:X}..0x{end:X}")
        raw = bytes(executable[start:end])
    elif row.get("byte_length") is not None:
        length = _int(row["byte_length"], "byte_length")
        if length <= 0 or start + length > len(executable):
            raise ValueError(f"invalid guarded record length at 0x{start:X}")
        raw = bytes(executable[start:start + length])
    else:
        raw = record_bytes(executable, start, grammar=grammar)
    expected_hash = row.get("raw_sha256") or row.get("source_sha256")
    if expected_hash and sha256(raw) != str(expected_hash).lower():
        raise ValueError(f"source SHA-256 changed for record at 0x{start:X}")
    return raw


def _span_boundaries(raw: bytes, *, grammar: str = RENDERER_GRAMMAR) -> set[int]:
    _end, tokens = _parse_record(raw, 0, len(raw), grammar)
    return {0, *(token.start for token in tokens), *(token.end for token in tokens)}


def apply_span_replacements(
    raw: bytes,
    replacements: list[dict[str, Any]],
    glyph_map: dict[str, int],
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> bytes:
    if not replacements:
        return raw
    parsed_end, record_tokens = _parse_record(raw, 0, len(raw), grammar)
    if parsed_end != len(raw):
        raise ValueError("bytes follow UI record terminator")
    boundaries = {0, *(token.start for token in record_tokens), *(token.end for token in record_tokens)}
    source_controls = control_signature(raw, grammar=grammar)
    ordered = sorted(replacements, key=lambda item: _int(item["relative_start"], "relative_start"))
    output = bytearray()
    cursor = 0
    for replacement in ordered:
        start = _int(replacement["relative_start"], "relative_start")
        end = _int(
            replacement.get("relative_end", replacement.get("relative_end_exclusive")),
            "relative_end",
        )
        if start < cursor or end < start or end > len(raw) - 1:
            raise ValueError(f"invalid or overlapping UI replacement span {start}..{end}")
        if start not in boundaries or end not in boundaries:
            raise ValueError(f"replacement span {start}..{end} splits a renderer token")
        source_hex = replacement.get("source_hex")
        source = raw[start:end] if source_hex is None else bytes.fromhex(str(source_hex))
        if raw[start:end] != source:
            raise ValueError(f"replacement source bytes differ at span {start}..{end}")
        expected_source_hash = replacement.get("source_sha256")
        if expected_source_hash and sha256(source) != str(expected_source_hash).lower():
            raise ValueError(f"replacement source SHA-256 differs at span {start}..{end}")
        span_tokens = [
            token
            for token in record_tokens
            if token.start < end and token.end > start
        ]
        if not span_tokens or any(token.kind != "glyph" for token in span_tokens):
            raise ValueError("replacement span contains a UI control or compact-mode data")
        output.extend(raw[cursor:start])
        output.extend(encode_ui_text(str(replacement["korean_text"]), glyph_map, terminate=False))
        cursor = end
    output.extend(raw[cursor:])
    rebuilt = bytes(output)
    if control_signature(rebuilt, grammar=grammar) != source_controls:
        raise ValueError("UI replacement changed renderer controls")
    return rebuilt


def rebuild_row_record(
    raw: bytes,
    row: dict[str, Any],
    glyph_map: dict[str, int],
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> bytes:
    replacements = row.get("replacements")
    if isinstance(replacements, list):
        return apply_span_replacements(raw, replacements, glyph_map, grammar=grammar)
    korean = row.get("korean_text")
    if korean is None:
        return raw
    if control_signature(raw, grammar=grammar) and not row.get("allow_full_record_rebuild"):
        raise ValueError("control-bearing row needs exact span replacements")
    rebuilt = encode_ui_text(str(korean), glyph_map, terminate=True)
    if (
        not row.get("allow_control_change")
        and control_signature(raw, grammar=grammar)
        != control_signature(rebuilt, grammar=grammar)
    ):
        raise ValueError("full UI record rebuild changed controls")
    return rebuilt


@dataclass
class StaticPool:
    """Best-fit allocator over guarded, possibly fragmented record spans."""

    spans: list[tuple[int, int]]
    free: list[tuple[int, int]] = field(init=False)
    allocations: list[dict[str, Any]] = field(default_factory=list)
    interned: dict[bytes, int] = field(default_factory=dict)
    payloads: dict[int, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ordered = sorted(self.spans)
        merged: list[tuple[int, int]] = []
        for start, end in ordered:
            if not 0 <= start < end <= RETAIL_FILE_BYTES:
                raise ValueError(f"invalid static-pool span 0x{start:X}..0x{end:X}")
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        self.spans = merged
        self.free = list(merged)

    @property
    def capacity(self) -> int:
        return sum(end - start for start, end in self.spans)

    @property
    def used(self) -> int:
        return sum(len(raw) for raw in self.payloads.values())

    def add(
        self,
        raw: bytes,
        *,
        asset_id: str,
        key: str,
        intern: bool = True,
        alignment: int = 1,
    ) -> int:
        if not raw:
            raise ValueError("cannot allocate an empty static payload")
        if alignment <= 0 or alignment & (alignment - 1):
            raise ValueError("alignment must be a positive power of two")
        if intern and raw in self.interned:
            return self.interned[raw]

        candidates: list[tuple[int, int, int, int]] = []
        for ordinal, (start, end) in enumerate(self.free):
            aligned = align_up(start, alignment)
            if aligned + len(raw) <= end:
                candidates.append((end - start, aligned - start, ordinal, aligned))
        if not candidates:
            raise ValueError(
                f"static record pool has no {len(raw)}-byte span for {asset_id}:{key}"
            )
        _span_size, _padding, ordinal, offset = min(candidates)
        start, end = self.free.pop(ordinal)
        replacement: list[tuple[int, int]] = []
        if start < offset:
            replacement.append((start, offset))
        payload_end = offset + len(raw)
        if payload_end < end:
            replacement.append((payload_end, end))
        self.free.extend(replacement)
        self.free.sort()

        self.payloads[offset] = raw
        self.allocations.append(
            {
                "asset_id": asset_id,
                "key": key,
                "file_offset": offset,
                "ram_address": PSX_FILE_TO_RAM_BIAS + offset,
                "size": len(raw),
                "sha256": sha256(raw),
            }
        )
        if intern:
            self.interned[raw] = offset
        return offset

    def commit(self, executable: bytearray) -> None:
        occupied: list[tuple[int, int]] = []
        for offset, raw in sorted(self.payloads.items()):
            end = offset + len(raw)
            if occupied and offset < occupied[-1][1]:
                raise AssertionError("static-pool allocations overlap")
            occupied.append((offset, end))
            executable[offset:end] = raw
        for offset, raw in self.payloads.items():
            if executable[offset:offset + len(raw)] != raw:
                raise AssertionError("static-pool payload verification failed")


def _merge_intervals(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if start >= end:
            raise ValueError(f"invalid interval 0x{start:X}..0x{end:X}")
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_intervals(
    spans: Iterable[tuple[int, int]],
    removed: Iterable[tuple[int, int]],
) -> list[tuple[int, int]]:
    cuts = _merge_intervals(removed)
    output: list[tuple[int, int]] = []
    for start, end in _merge_intervals(spans):
        cursor = start
        for cut_start, cut_end in cuts:
            if cut_end <= cursor:
                continue
            if cut_start >= end:
                break
            if cursor < cut_start:
                output.append((cursor, min(cut_start, end)))
            cursor = max(cursor, cut_end)
            if cursor >= end:
                break
        if cursor < end:
            output.append((cursor, end))
    return output


def _guard_untracked_donor_references(
    executable: bytes | bytearray,
    donor_spans: Iterable[tuple[int, int]],
    donor_record_spans: Iterable[tuple[int, int]],
    known_pointer_fields: set[int],
) -> tuple[list[tuple[int, int]], list[tuple[int, int, bytes]], dict[str, Any]]:
    """Exclude records hit by any untracked aligned self-relative word.

    SECOND contains secondary alias/resource tables that are absent from the
    authoritative UI inventories.  Some fields point into the middle of a
    renderer record.  Reusing such a record would corrupt that secondary
    reader even though every primary UI pointer was repointed correctly.

    The scan intentionally accepts false positives: preserving a few extra
    source records is safe, while overwriting one real hidden target is not.
    Candidate fields located inside donor text are ignored because their four
    glyph bytes are not pointer storage.
    """

    merged_donors = _merge_intervals(donor_spans)
    records = list(donor_record_spans)

    def contains(spans: Iterable[tuple[int, int]], offset: int) -> bool:
        return any(start <= offset < end for start, end in spans)

    candidate_fields: list[int] = []
    candidate_targets: list[int] = []
    protected_records: list[tuple[int, int]] = []
    for field in range(0, len(executable) - 3, 4):
        if field in known_pointer_fields or contains(merged_donors, field):
            continue
        target = field + s32(executable, field)
        if not contains(merged_donors, target):
            continue
        owners = [
            (start, end)
            for start, end in records
            if start <= target < end
        ]
        if not owners:
            raise ValueError(
                f"untracked relative field 0x{field:X} targets donor byte "
                f"0x{target:X} outside every guarded record"
            )
        candidate_fields.append(field)
        candidate_targets.append(target)
        protected_records.extend(owners)

    protected = _merge_intervals(protected_records)
    safe_spans = _subtract_intervals(merged_donors, protected)
    snapshots = [
        (start, end, bytes(executable[start:end]))
        for start, end in protected
    ]
    return safe_spans, snapshots, {
        "aligned_untracked_reference_count": len(candidate_fields),
        "protected_record_span_count": len(protected),
        "protected_record_bytes": sum(end - start for start, end in protected),
        "candidate_fields": candidate_fields,
        "candidate_targets": candidate_targets,
    }


def _guard_static_font_donor(
    executable: bytes | bytearray,
    glyph_map: dict[str, int],
) -> list[tuple[int, int]]:
    forbidden = [
        (char, index)
        for char, index in glyph_map.items()
        if STATIC_FONT_DONOR_GLYPH_START <= index < STATIC_FONT_DONOR_GLYPH_END
    ]
    if forbidden:
        rendered = ", ".join(f"{char!r}=0x{index:X}" for char, index in forbidden[:8])
        raise ValueError(f"static font-donor glyphs are assigned by final map: {rendered}")
    raw = bytes(executable[STATIC_FONT_DONOR_START:STATIC_FONT_DONOR_END])
    if sha256(raw) != STATIC_FONT_DONOR_SHA256:
        raise ValueError("SECOND guarded static font-donor tail changed")
    expected_bytes = (
        STATIC_FONT_DONOR_GLYPH_END - STATIC_FONT_DONOR_GLYPH_START
    ) * 32
    if len(raw) != expected_bytes:
        raise AssertionError("static font-donor glyph/file bounds disagree")
    spans: list[tuple[int, int]] = []
    cursor = STATIC_FONT_DONOR_START
    for index in STATIC_FONT_DONOR_EXCLUDED_GLYPHS:
        glyph_start = STATIC_FONT_DONOR_START + (
            index - STATIC_FONT_DONOR_GLYPH_START
        ) * 32
        if cursor < glyph_start:
            spans.append((cursor, glyph_start))
        cursor = glyph_start + 32
    if cursor < STATIC_FONT_DONOR_END:
        spans.append((cursor, STATIC_FONT_DONOR_END))
    return spans


def _font_tail_glyph_references(
    raw: bytes,
    *,
    grammar: str,
    stream: bool = False,
) -> set[int]:
    references: set[int] = set()
    cursor = 0
    while cursor < len(raw):
        end, tokens = _parse_record(raw, cursor, len(raw), grammar)
        for token in tokens:
            if token.kind != "glyph" or len(token.raw) != 2:
                continue
            index = ((token.raw[0] - 0xEB) << 8) | token.raw[1]
            if STATIC_FONT_DONOR_GLYPH_START <= index < STATIC_FONT_DONOR_GLYPH_END:
                references.add(index)
        cursor = end
        if not stream:
            if cursor != len(raw):
                raise ValueError("bytes follow final-UI renderer record")
            break
    return references


def _audit_allowed_changes(
    source: bytes,
    patched: bytes | bytearray,
    allowed_spans: Iterable[tuple[int, int]],
) -> dict[str, Any]:
    if len(source) != len(patched):
        raise AssertionError("UI repack changed executable length before change audit")
    merged = _merge_intervals(allowed_spans)
    span_index = 0
    changed = 0
    unexpected: list[int] = []
    for offset, (before, after) in enumerate(zip(source, patched)):
        if before == after:
            continue
        changed += 1
        while span_index < len(merged) and merged[span_index][1] <= offset:
            span_index += 1
        if (
            span_index >= len(merged)
            or not merged[span_index][0] <= offset < merged[span_index][1]
        ):
            unexpected.append(offset)
    if unexpected:
        rendered = ", ".join(f"0x{offset:X}" for offset in unexpected[:8])
        raise AssertionError(f"UI repack changed bytes outside its write envelope: {rendered}")
    return {
        "changed_bytes": changed,
        "allowed_range_count": len(merged),
        "allowed_range_bytes": sum(end - start for start, end in merged),
        "unexpected_changed_bytes": 0,
    }


def patch_relative_pointer(executable: bytearray, field_offset: int, target_offset: int) -> None:
    relative = target_offset - field_offset
    if not -(1 << 31) <= relative < (1 << 31):
        raise ValueError("self-relative pointer does not fit signed 32 bits")
    executable[field_offset:field_offset + 4] = struct.pack("<i", relative)
    if field_offset + s32(executable, field_offset) != target_offset:
        raise AssertionError("self-relative pointer verification failed")


def load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"overlay is not a JSON object: {path}")
    return document


def _walk_records(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if (
            ("source_offset" in value or "target" in value)
            and ("entry_index" in value or "sequential_index" in value or "pointer_field" in value)
            and ("korean_text" in value or "replacements" in value or "status" in value)
        ):
            yield value
            return
        for child in value.values():
            yield from _walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child)


def overlay_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one normalised row shape for all maintained overlay schemas."""

    asset_aliases = {
        "common_preview_pool": "common_preview_and_conditions_pool",
        "common_music_demo_pool": "common_music_demo_title_pool",
        "second_ui_master": "second_ui_script_master",
    }

    if document.get("schema") == "srwcb-second-ui-names-overlay-v1":
        id_map = {
            "short_pilot_names": "pilot_short_names",
            "full_pilot_names": "pilot_full_names",
            "unit_names": "unit_names",
        }
        rows: list[dict[str, Any]] = []
        for table in document.get("tables", []):
            asset_id = id_map.get(str(table.get("id")), str(table.get("id")))
            for source in table.get("rows", []):
                rows.append(
                    {
                        "asset_id": asset_id,
                        "entry_index": source["index"],
                        "pointer_field": source["pointer_field_offset"],
                        "source_offset": source["target_offset"],
                        "raw_hex": source.get("source_raw_hex"),
                        "raw_sha256": source["source_raw_sha256"],
                        "japanese_text": source.get("japanese"),
                        "korean_text": source["korean"],
                        "status": "translated",
                        "review": source.get("review"),
                    }
                )
        return rows

    if document.get("schema") == "srwcb-second-ui-tables-overlay-v1":
        rows = []
        for table in document.get("tables", []):
            asset_id = str(table["asset_id"])
            for source in table.get("entries", []):
                row = dict(source)
                row["asset_id"] = asset_id
                row["entry_index"] = source["index"]
                # Description records use F6 line separators.  The reviewed
                # overlay represents those separators as literal newlines;
                # rebuild_row_record still verifies the exact control
                # signature after encoding.
                if source.get("control_signature"):
                    row["allow_full_record_rebuild"] = True
                rows.append(row)
        return rows

    rows = list(_walk_records(document))
    for row in rows:
        asset_id = str(row.get("asset_id", ""))
        if asset_id in asset_aliases:
            row["asset_id"] = asset_aliases[asset_id]
    return rows


def collect_korean_ui_texts(paths: Iterable[Path]) -> list[str]:
    texts: list[str] = []
    for path in paths:
        document = load_json(path)
        for row in overlay_records(document):
            korean = row.get("korean_text")
            if isinstance(korean, str):
                texts.append(korean)
            replacements = row.get("replacements")
            if isinstance(replacements, list):
                texts.extend(
                    str(item["korean_text"])
                    for item in replacements
                    if isinstance(item, dict) and isinstance(item.get("korean_text"), str)
                )
    return texts


def _record_identity(row: dict[str, Any]) -> tuple[str, int]:
    asset = str(row.get("asset_id") or row.get("table_id") or row.get("group") or "")
    if "sequential_index" in row:
        index = _int(row["sequential_index"], "sequential_index")
    else:
        index = _int(row.get("entry_index", -1), "entry_index")
    return asset, index


def _translation_map(documents: Iterable[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for document in documents:
        for row in overlay_records(document):
            identity = _record_identity(row)
            if not identity[0]:
                continue
            if identity in result and result[identity] != row:
                raise ValueError(f"duplicate UI overlay identity {identity}")
            result[identity] = row
    return result


def _mips_lui_lw_pair(register: int, address: int) -> bytes:
    if not 0 <= register < 32 or not 0 <= address <= 0xFFFFFFFF:
        raise ValueError("invalid MIPS register/address")
    high = ((address + 0x8000) >> 16) & 0xFFFF
    low = address & 0xFFFF
    lui = 0x3C000000 | (register << 16) | high
    lw = 0x8C000000 | (register << 21) | (register << 16) | low
    return struct.pack("<II", lui, lw)


def _patch_sequential_preview(
    executable: bytearray,
    inventory: dict[str, Any],
    translations: dict[tuple[str, int], dict[str, Any]],
    glyph_map: dict[str, int],
) -> dict[str, Any]:
    group = inventory["common_preview_pool"]
    asset_id = str(group["asset_id"])
    rebuilt = bytearray()
    translated = 0
    for source_row in group["records"]:
        index = _int(source_row["sequential_index"], "sequential_index")
        raw = _verify_record_guard(executable, source_row)
        overlay = translations.get((asset_id, index))
        if source_row.get("translation_target") and overlay is None:
            raise ValueError(f"missing preview translation {asset_id}[{index}]")
        if overlay is not None:
            raw = rebuild_row_record(raw, overlay, glyph_map)
            translated += 1
        rebuilt.extend(raw)
    pool_start = _int(group["pool_start"], "pool_start")
    pool_end = _int(group["pool_end"], "pool_end")
    capacity = pool_end - pool_start
    if u32(executable, ROOT_RESOURCE_HEADER) != PSX_FILE_TO_RAM_BIAS + ROOT_RESOURCE_HEADER + 4:
        raise ValueError("SECOND root resource header changed")
    if ROOT_SCRIPT_ENTRY3_FIELD + s32(executable, ROOT_SCRIPT_ENTRY3_FIELD) != ROOT_SCRIPT_ENTRY3_START:
        raise ValueError("SECOND root script 3 field changed")
    source_script = bytes(executable[ROOT_SCRIPT_ENTRY3_START:ROOT_SCRIPT_ENTRY3_END])
    if sha256(source_script) != ROOT_SCRIPT_ENTRY3_SHA256:
        raise ValueError("SECOND root script 3 source changed")
    if not ROOT_SCRIPT_ENTRY3_START < pool_start < pool_end <= ROOT_SCRIPT_ENTRY3_END:
        raise ValueError("preview pool no longer lies at the end of root script 3")

    # Script 3 must remain one contiguous VM block.  Relocate the old root
    # header/entry-prefix block to unused font slots, then grow script 3
    # backwards into the vacated 0xE14..0x1230 region.  This avoids both an EXE
    # extension and any runtime RAM reservation.
    relocated_script = (
        bytes(executable[ROOT_SCRIPT_ENTRY3_START:pool_start])
        + bytes(rebuilt)
        + bytes(executable[pool_end:ROOT_SCRIPT_ENTRY3_END])
    )
    script_capacity = ROOT_SCRIPT_ENTRY3_END - ROOT_RESOURCE_HEADER
    if len(relocated_script) > script_capacity:
        raise ValueError(
            f"rebuilt root script 3 needs {len(relocated_script)} bytes; "
            f"in-image capacity is {script_capacity}"
        )

    prefix = bytes(executable[ROOT_PREFIX_BLOCK_START:ROOT_PREFIX_BLOCK_END])
    if sha256(prefix) != ROOT_PREFIX_BLOCK_SHA256:
        raise ValueError("SECOND root prefix block changed")
    cave = bytes(executable[ROOT_RELOCATION_CAVE_START:ROOT_RELOCATION_CAVE_END])
    if sha256(cave) != ROOT_RELOCATION_CAVE_SHA256:
        raise ValueError("SECOND guarded font cave changed")
    if len(prefix) > len(cave):
        raise AssertionError("root prefix does not fit guarded font cave")
    forbidden = [
        (char, index)
        for char, index in glyph_map.items()
        if 0xA47 <= index < 0xA68
    ]
    if forbidden:
        rendered = ", ".join(f"{char!r}=0x{index:X}" for char, index in forbidden[:8])
        raise ValueError(f"font cave glyphs are assigned by final map: {rendered}")

    relocated_prefix = bytearray(prefix)
    relocated_table = PSX_FILE_TO_RAM_BIAS + ROOT_RELOCATION_CAVE_START + 4
    struct.pack_into("<I", relocated_prefix, 0, relocated_table)
    relocated_entry3_field = ROOT_RELOCATION_CAVE_START + 0x10
    entry3_relative = ROOT_RESOURCE_HEADER - relocated_entry3_field
    struct.pack_into("<i", relocated_prefix, 0x10, entry3_relative)
    executable[
        ROOT_RELOCATION_CAVE_START:
        ROOT_RELOCATION_CAVE_START + len(relocated_prefix)
    ] = relocated_prefix
    executable[
        ROOT_RELOCATION_CAVE_START + len(relocated_prefix):
        ROOT_RELOCATION_CAVE_END
    ] = b"\x00" * (len(cave) - len(relocated_prefix))

    patched_sites: list[dict[str, Any]] = []
    relocated_header_ram = PSX_FILE_TO_RAM_BIAS + ROOT_RELOCATION_CAVE_START
    for offset, register in ROOT_HEADER_LOAD_SITES:
        expected = _mips_lui_lw_pair(register, PSX_FILE_TO_RAM_BIAS + ROOT_RESOURCE_HEADER)
        if executable[offset:offset + len(expected)] != expected:
            raise ValueError(f"root-header load changed at 0x{offset:X}")
        patched = _mips_lui_lw_pair(register, relocated_header_ram)
        executable[offset:offset + len(patched)] = patched
        patched_sites.append(
            {
                "file_offset": offset,
                "register": register,
                "source": expected.hex(" ").upper(),
                "patched": patched.hex(" ").upper(),
            }
        )
    executable[ROOT_RESOURCE_HEADER:ROOT_SCRIPT_ENTRY3_END] = (
        relocated_script
        + b"\x00" * (script_capacity - len(relocated_script))
    )
    if executable[ROOT_RESOURCE_HEADER:ROOT_RESOURCE_HEADER + len(relocated_script)] != relocated_script:
        raise AssertionError("root script 3 in-image relocation failed")

    relocated_header = ROOT_RELOCATION_CAVE_START
    if u32(executable, relocated_header) != relocated_table:
        raise AssertionError("relocated root header self-pointer is wrong")
    relocated_target = relocated_entry3_field + s32(executable, relocated_entry3_field)
    if relocated_target != ROOT_RESOURCE_HEADER:
        raise AssertionError("relocated root entry 3 target is wrong")
    return {
        "asset_id": asset_id,
        "record_count": len(group["records"]),
        "translated_records": translated,
        "source_capacity": capacity,
        "rebuilt_bytes": len(rebuilt),
        "growth_bytes": len(rebuilt) - capacity,
        "root_script_source_start": ROOT_SCRIPT_ENTRY3_START,
        "root_script_source_end": ROOT_SCRIPT_ENTRY3_END,
        "root_script_relocated_start": ROOT_RESOURCE_HEADER,
        "root_script_relocated_bytes": len(relocated_script),
        "root_script_capacity": script_capacity,
        "root_script_slack_bytes": script_capacity - len(relocated_script),
        "root_header_relocated_start": ROOT_RELOCATION_CAVE_START,
        "root_header_relocated_end": ROOT_RELOCATION_CAVE_START + len(relocated_prefix),
        "root_header_load_patches": patched_sites,
        "root_pointer_field": relocated_entry3_field,
        "storage": "complete root VM script 3 repacked into vacated retail-image prefix",
        "sha256": sha256(bytes(rebuilt)),
    }


def _prepare_sequential_music_pool(
    executable: bytearray,
    inventory: dict[str, Any],
    translations: dict[tuple[str, int], dict[str, Any]],
    glyph_map: dict[str, int],
) -> tuple[bytes, list[tuple[int, int]], dict[str, Any]]:
    group = inventory["common_music_demo_pool"]
    asset_id = str(group["asset_id"])
    rebuilt_pool = bytearray()
    old_to_relative: dict[int, int] = {}
    translated = 0
    for source_row in group["records"]:
        index = _int(source_row["sequential_index"], "sequential_index")
        source_offset = _int(source_row["source_offset"], "source_offset")
        raw = _verify_record_guard(executable, source_row)
        overlay = translations.get((asset_id, index))
        if source_row.get("translation_target") and overlay is None:
            raise ValueError(f"missing music/demo translation {asset_id}[{index}]")
        if overlay is not None:
            raw = rebuild_row_record(raw, overlay, glyph_map)
            translated += 1
        old_to_relative[source_offset] = len(rebuilt_pool)
        rebuilt_pool.extend(raw)

    pointer_count = 0
    pointer_updates: list[tuple[int, int]] = []
    for source_row in group["records"]:
        relative_target = old_to_relative[_int(source_row["source_offset"], "source_offset")]
        for pointer_field in source_row.get("pointer_fields", []):
            field = _int(pointer_field, "pointer_field")
            expected_source = _int(source_row["source_offset"], "source_offset")
            if field + s32(executable, field) != expected_source:
                raise ValueError(
                    f"music/demo pointer 0x{field:X} no longer targets "
                    f"0x{expected_source:X}"
                )
            pointer_updates.append((field, relative_target))
            pointer_count += 1
    if pointer_count != _int(group["nested_entry_count"], "nested_entry_count"):
        raise ValueError(
            f"music/demo pointer coverage {pointer_count} != {group['nested_entry_count']}"
        )
    return bytes(rebuilt_pool), pointer_updates, {
        "asset_id": asset_id,
        "record_count": len(group["records"]),
        "translated_records": translated,
        "pointer_count": pointer_count,
        "sequential_only_records": _int(group["sequential_only_record_count"], "sequential_only_record_count"),
        "rebuilt_bytes": len(rebuilt_pool),
        "storage": "contiguous pool in guarded original record spans",
    }


def _prepare_pointer_group(
    executable: bytearray,
    asset_id: str,
    source_rows: list[dict[str, Any]],
    translations: dict[tuple[str, int], dict[str, Any]],
    glyph_map: dict[str, int],
    *,
    require_all_targets: bool,
    repack_untranslated: bool = True,
) -> tuple[list[dict[str, Any]], list[tuple[int, int]], dict[str, Any]]:
    translated = 0
    planned: list[dict[str, Any]] = []
    source_spans: list[tuple[int, int]] = []
    grammar = (
        SECOND_UI_VM_GRAMMAR
        if asset_id == "second_ui_script_master"
        else RENDERER_GRAMMAR
    )
    for source_row in source_rows:
        index = _int(source_row["entry_index"], "entry_index")
        overlay = translations.get((asset_id, index))
        required = bool(source_row.get("translation_target")) and require_all_targets
        if required and overlay is None:
            raise ValueError(f"missing table translation {asset_id}[{index}]")
        if overlay is None and not repack_untranslated:
            continue

        classification = str(source_row.get("classification", ""))
        guarded = any(
            source_row.get(field) is not None
            for field in ("raw_hex", "source_hex", "raw_sha256", "source_sha256")
        )
        if classification == "non_ff_terminated_target" or not guarded:
            if overlay is not None:
                raise ValueError(f"overlay targets structural table entry {asset_id}[{index}]")
            continue

        source_offset = _int(source_row.get("source_offset", source_row.get("target")), "source_offset")
        source_raw = _verify_record_guard(executable, source_row, grammar=grammar)
        if not source_raw.endswith(b"\xFF"):
            raise ValueError(f"guarded UI record at 0x{source_offset:X} has no FF terminator")
        rebuilt = (
            rebuild_row_record(source_raw, overlay, glyph_map, grammar=grammar)
            if overlay is not None
            else source_raw
        )
        field_value = (
            overlay.get("pointer_field", source_row.get("pointer_field"))
            if overlay is not None
            else source_row.get("pointer_field")
        )
        if field_value is None:
            raise ValueError(f"{asset_id}[{index}] has no pointer field")
        field = _int(field_value, "pointer_field")
        if field + s32(executable, field) != source_offset:
            raise ValueError(
                f"{asset_id}[{index}] pointer 0x{field:X} no longer targets "
                f"guarded source 0x{source_offset:X}"
            )
        planned.append(
            {
                "asset_id": asset_id,
                "entry_index": index,
                "pointer_field": field,
                "source_offset": source_offset,
                "raw": rebuilt,
                "translated": overlay is not None,
            }
        )
        source_spans.append((source_offset, source_offset + len(source_raw)))
        if overlay is not None:
            translated += 1
    return planned, source_spans, {
        "asset_id": asset_id,
        "source_entries": len(source_rows),
        "translated_entries": translated,
        "guarded_record_entries": len(planned),
        "unique_rebuilt_records": len({row["raw"] for row in planned}),
        "patched_pointers": len(planned),
    }


def _inventory_table_map(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {
        str(table["asset_id"]): table
        for table in inventory["other_menu_visible_tables"]
    }
    result[str(inventory["second_ui_master"]["asset_id"])] = inventory["second_ui_master"]
    return result


def _prepare_overlay_only_group(
    executable: bytearray,
    asset_id: str,
    rows: list[dict[str, Any]],
    glyph_map: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    in_place: list[dict[str, Any]] = []
    relocated: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: _record_identity(item)[1]):
        _asset, index = _record_identity(row)
        source_offset = _int(row.get("source_offset", row.get("target")), "source_offset")
        source_raw = _verify_record_guard(executable, row)
        rebuilt = rebuild_row_record(source_raw, row, glyph_map)
        field_value = row.get("pointer_field")
        if field_value is None:
            raise ValueError(f"{asset_id}[{index}] has no pointer field")
        field = _int(field_value, "pointer_field")
        if field + s32(executable, field) != source_offset:
            raise ValueError(
                f"{asset_id}[{index}] pointer 0x{field:X} no longer targets "
                f"0x{source_offset:X}"
            )
        plan = {
            "asset_id": asset_id,
            "entry_index": index,
            "pointer_field": field,
            "source_offset": source_offset,
            "source_size": len(source_raw),
            "raw": rebuilt,
            "translated": True,
        }
        if len(rebuilt) == len(source_raw):
            in_place.append(plan)
        else:
            relocated.append(plan)
    return in_place, relocated, {
        "asset_id": asset_id,
        "source_entries": len(rows),
        "translated_entries": len(rows),
        "in_place_entries": len(in_place),
        "relocated_entries": len(relocated),
        "patched_pointers": len(rows),
    }


def _patch_common_audio_option_width(executable: bytearray) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for offset, source, patched in COMMON_AUDIO_OPTION_WIDTH_PATCHES:
        end = offset + len(source)
        if executable[offset:end] != source:
            raise ValueError(
                f"common audio option-width instruction changed at 0x{offset:X}"
            )
        executable[offset:end] = patched
        rows.append(
            {
                "file_offset": offset,
                "source": source.hex(" ").upper(),
                "patched": patched.hex(" ").upper(),
            }
        )
    return {
        "asset_id": "common_ui_option_width_code",
        "patched_call_sites": len(rows),
        "source_group_bytes": 4,
        "patched_group_bytes": 8,
        "patches": rows,
    }


def patch_second_executable_ui(
    executable_root: Path,
    glyph_map: dict[str, int],
    inventory_path: Path,
    overlay_paths: Iterable[Path],
) -> dict[str, Any]:
    """Apply all approved SECOND menu overlays without extending SECOND.WAR."""

    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw.decode("utf-8"))
    documents = [load_json(Path(path)) for path in overlay_paths]
    translations = _translation_map(documents)

    executable_path = executable_root / SECOND_EXECUTABLE
    executable = bytearray(executable_path.read_bytes())
    if executable[:8] != PSX_EXE_MAGIC:
        raise ValueError("SECOND.WAR is not a PS-X EXE")
    if len(executable) != RETAIL_FILE_BYTES or u32(executable, 0x1C) != RETAIL_T_SIZE:
        raise ValueError("SECOND.WAR must have its retail image size before UI repack")
    source_executable = bytes(executable)

    music_raw, music_pointer_updates, music_manifest = _prepare_sequential_music_pool(
        executable, inventory, translations, glyph_map
    )

    table_map = _inventory_table_map(inventory)
    pointer_plans: list[dict[str, Any]] = []
    source_spans: list[tuple[int, int]] = []
    donor_record_spans: list[tuple[int, int]] = []
    table_manifests: list[dict[str, Any]] = []
    for asset_id, table in table_map.items():
        planned, spans, manifest = _prepare_pointer_group(
            executable,
            asset_id,
            list(table["records"]),
            translations,
            glyph_map,
            require_all_targets=True,
            repack_untranslated=asset_id != str(inventory["second_ui_master"]["asset_id"]),
        )
        pointer_plans.extend(planned)
        if asset_id != str(inventory["second_ui_master"]["asset_id"]):
            source_spans.extend(spans)
            donor_record_spans.extend(spans)
        table_manifests.append(manifest)

    music_group = inventory["common_music_demo_pool"]
    music_source_start = _int(music_group["pool_start"], "pool_start")
    music_source_end = _int(music_group["pool_end"], "pool_end")
    if music_source_end - music_source_start != _int(music_group["pool_bytes"], "pool_bytes"):
        raise ValueError("music/demo source-pool bounds changed")
    source_spans.append((music_source_start, music_source_end))
    for source_row in music_group["records"]:
        source_offset = _int(source_row["source_offset"], "source_offset")
        source_raw = _verify_record_guard(executable, source_row)
        donor_record_spans.append(
            (source_offset, source_offset + len(source_raw))
        )

    # Common-master overlay rows are a mixed resource, not a reclaimable
    # string table.  Equal-size records stay in place; only growing rows use
    # the guarded donor spans collected from complete pointer-backed tables.
    known_assets = set(table_map) | {
        str(inventory["common_preview_pool"]["asset_id"]),
        str(inventory["common_music_demo_pool"]["asset_id"]),
    }
    overlay_only: dict[str, list[dict[str, Any]]] = {}
    for row in translations.values():
        asset_id, _index = _record_identity(row)
        if asset_id in known_assets:
            continue
        overlay_only.setdefault(asset_id, []).append(row)
    common_in_place: list[dict[str, Any]] = []
    common_relocated: list[dict[str, Any]] = []
    common_manifests: list[dict[str, Any]] = []
    for asset_id, rows in sorted(overlay_only.items()):
        in_place, relocated, manifest = _prepare_overlay_only_group(
            executable, asset_id, rows, glyph_map
        )
        common_in_place.extend(in_place)
        common_relocated.extend(relocated)
        common_manifests.append(manifest)

    # Audit every final inventory-backed UI record before borrowing font
    # pixels.  Translated master rows are in pointer_plans; untranslated
    # master rows remain at their guarded source locations and must be checked
    # separately.  Preview/music records are sequential rather than leaf-
    # pointer plans, so materialise/parse those streams here as well.
    final_ui_tail_glyphs: set[int] = set()
    for row in pointer_plans:
        grammar = (
            SECOND_UI_VM_GRAMMAR
            if row["asset_id"] == "second_ui_script_master"
            else RENDERER_GRAMMAR
        )
        final_ui_tail_glyphs.update(
            _font_tail_glyph_references(bytes(row["raw"]), grammar=grammar)
        )
    planned_master_indices = {
        int(row["entry_index"])
        for row in pointer_plans
        if row["asset_id"] == "second_ui_script_master"
    }
    for source_row in inventory["second_ui_master"]["records"]:
        index = _int(source_row["entry_index"], "entry_index")
        if index in planned_master_indices:
            continue
        classification = str(source_row.get("classification", ""))
        guarded = any(
            source_row.get(field) is not None
            for field in ("raw_hex", "source_hex", "raw_sha256", "source_sha256")
        )
        if classification == "non_ff_terminated_target" or not guarded:
            continue
        final_ui_tail_glyphs.update(
            _font_tail_glyph_references(
                _verify_record_guard(
                    executable,
                    source_row,
                    grammar=SECOND_UI_VM_GRAMMAR,
                ),
                grammar=SECOND_UI_VM_GRAMMAR,
            )
        )
    final_ui_tail_glyphs.update(
        _font_tail_glyph_references(
            music_raw,
            grammar=RENDERER_GRAMMAR,
            stream=True,
        )
    )
    preview_group = inventory["common_preview_pool"]
    preview_asset_id = str(preview_group["asset_id"])
    for source_row in preview_group["records"]:
        index = _int(source_row["sequential_index"], "sequential_index")
        raw = _verify_record_guard(executable, source_row)
        overlay = translations.get((preview_asset_id, index))
        if overlay is not None:
            raw = rebuild_row_record(raw, overlay, glyph_map)
        final_ui_tail_glyphs.update(
            _font_tail_glyph_references(raw, grammar=RENDERER_GRAMMAR)
        )
    for row in common_in_place + common_relocated:
        final_ui_tail_glyphs.update(
            _font_tail_glyph_references(bytes(row["raw"]), grammar=RENDERER_GRAMMAR)
        )
    unpreserved_tail_glyphs = final_ui_tail_glyphs - set(
        STATIC_FONT_DONOR_EXCLUDED_GLYPHS
    )
    if unpreserved_tail_glyphs:
        rendered = ", ".join(
            f"0x{index:X}" for index in sorted(unpreserved_tail_glyphs)
        )
        raise ValueError(f"final UI still references static font-donor glyphs: {rendered}")

    pointer_fields = {
        int(row["pointer_field"])
        for row in pointer_plans
    } | {
        int(row["pointer_field"])
        for row in common_in_place + common_relocated
    } | {field for field, _relative in music_pointer_updates}

    safe_source_spans, protected_donor_snapshots, hidden_reference_guard = (
        _guard_untracked_donor_references(
            executable,
            source_spans,
            donor_record_spans,
            pointer_fields,
        )
    )
    static_font_donors = _guard_static_font_donor(executable, glyph_map)
    pool = StaticPool([*safe_source_spans, *static_font_donors])
    for field in pointer_fields:
        if any(max(start, field) < min(end, field + 4) for start, end in pool.spans):
            raise ValueError(f"static donor span overlaps pointer field 0x{field:X}")

    music_start = pool.add(
        music_raw,
        asset_id=str(inventory["common_music_demo_pool"]["asset_id"]),
        key="complete_ordered_pool",
        intern=False,
        alignment=4,
    )
    music_manifest["pool_start"] = music_start
    music_manifest["pool_end"] = music_start + len(music_raw)

    unique_plans: dict[bytes, dict[str, Any]] = {}
    for row in pointer_plans + common_relocated:
        unique_plans.setdefault(bytes(row["raw"]), row)
    target_by_raw: dict[bytes, int] = {}
    for raw, representative in sorted(
        unique_plans.items(), key=lambda item: (-len(item[0]), item[0])
    ):
        target_by_raw[raw] = pool.add(
            raw,
            asset_id=str(representative["asset_id"]),
            key=f"record[{representative['entry_index']}]",
            intern=True,
        )

    pool.commit(executable)
    for field, relative_target in music_pointer_updates:
        patch_relative_pointer(executable, field, music_start + relative_target)
    for row in pointer_plans + common_relocated:
        target = target_by_raw[bytes(row["raw"])]
        patch_relative_pointer(executable, int(row["pointer_field"]), target)
    for row in common_in_place:
        target = int(row["source_offset"])
        raw = bytes(row["raw"])
        executable[target:target + len(raw)] = raw
        patch_relative_pointer(executable, int(row["pointer_field"]), target)

    for row in pointer_plans + common_relocated + common_in_place:
        field = int(row["pointer_field"])
        target = field + s32(executable, field)
        raw = bytes(row["raw"])
        if executable[target:target + len(raw)] != raw:
            raise AssertionError(
                f"repacked UI pointer verification failed for "
                f"{row['asset_id']}[{row['entry_index']}]"
            )

    groups: list[dict[str, Any]] = []
    groups.append(_patch_sequential_preview(executable, inventory, translations, glyph_map))
    groups.append(music_manifest)
    groups.extend(table_manifests)
    groups.extend(common_manifests)

    groups.append(_patch_common_audio_option_width(executable))

    # Recheck after root relocation and code edits, not merely after the pool
    # commit.  This makes write ordering fail closed if a future layout change
    # causes either late patch to overlap a pointer field or payload.
    if executable[music_start:music_start + len(music_raw)] != music_raw:
        raise AssertionError("late UI patch overwrote the rebuilt music/demo pool")
    for field, relative_target in music_pointer_updates:
        if field + s32(executable, field) != music_start + relative_target:
            raise AssertionError(f"late UI patch changed music/demo pointer 0x{field:X}")
    for row in pointer_plans + common_relocated + common_in_place:
        field = int(row["pointer_field"])
        target = field + s32(executable, field)
        raw = bytes(row["raw"])
        if executable[target:target + len(raw)] != raw:
            raise AssertionError(
                f"late UI patch changed {row['asset_id']}[{row['entry_index']}]"
            )

    for start, end, source in protected_donor_snapshots:
        if executable[start:end] != source:
            raise AssertionError(
                f"hidden-reference source span 0x{start:X}..0x{end:X} was overwritten"
            )

    change_audit = _audit_allowed_changes(
        source_executable,
        executable,
        [
            *(
                (offset, offset + len(raw))
                for offset, raw in pool.payloads.items()
            ),
            *((field, field + 4) for field in pointer_fields),
            *(
                (
                    int(row["source_offset"]),
                    int(row["source_offset"]) + len(bytes(row["raw"])),
                )
                for row in common_in_place
            ),
            (ROOT_RESOURCE_HEADER, ROOT_SCRIPT_ENTRY3_END),
            (ROOT_RELOCATION_CAVE_START, ROOT_RELOCATION_CAVE_END),
            *((offset, offset + 8) for offset, _register in ROOT_HEADER_LOAD_SITES),
            *(
                (offset, offset + len(source))
                for offset, source, _patched in COMMON_AUDIO_OPTION_WIDTH_PATCHES
            ),
        ],
    )

    if len(executable) != RETAIL_FILE_BYTES or u32(executable, 0x1C) != RETAIL_T_SIZE:
        raise AssertionError("UI repack changed SECOND.WAR's retail load boundary")

    executable_path.write_bytes(executable)
    return {
        "format": "srwcb-second-executable-ui-static-repack-v2",
        "path": str(SECOND_EXECUTABLE).replace("\\", "/"),
        "inventory": {
            "path": str(inventory_path),
            "sha256": sha256(inventory_raw),
        },
        "overlays": [
            {"path": str(path), "sha256": sha256(Path(path).read_bytes())}
            for path in overlay_paths
        ],
        "storage": "guarded original record spans and unassigned font tail; no post-BSS arena",
        "hidden_relative_reference_guard": hidden_reference_guard,
        "static_font_donor": {
            "file_start": STATIC_FONT_DONOR_START,
            "file_end": STATIC_FONT_DONOR_END,
            "glyph_start": STATIC_FONT_DONOR_GLYPH_START,
            "glyph_end_exclusive": STATIC_FONT_DONOR_GLYPH_END,
            "excluded_glyphs": list(STATIC_FONT_DONOR_EXCLUDED_GLYPHS),
            "final_ui_tail_glyph_references": sorted(final_ui_tail_glyphs),
            "usable_spans": static_font_donors,
            "source_sha256": STATIC_FONT_DONOR_SHA256,
        },
        "change_audit": change_audit,
        "source_span_capacity": pool.capacity,
        "allocated_payload_bytes": pool.used,
        "remaining_source_span_bytes": pool.capacity - pool.used,
        "new_file_bytes": len(executable),
        "new_t_size": u32(executable, 0x1C),
        "new_loaded_end_ram": PSX_FILE_TO_RAM_BIAS + len(executable),
        "groups": groups,
        "allocations": pool.allocations,
        "executable_sha256_before_runtime_boundary_patch": sha256(bytes(executable)),
    }


__all__ = [
    "collect_korean_ui_texts",
    "patch_second_executable_ui",
    "encode_ui_text",
    "parse_renderer_record",
    "parse_second_ui_vm_record",
    "control_signature",
    "ROOT_RELOCATION_CAVE_START",
    "ROOT_RELOCATION_CAVE_END",
]
