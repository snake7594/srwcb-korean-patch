#!/usr/bin/env python3
"""Relocate SECOND.WAR's Korean menu resources into a guarded UI arena.

The retail executable ends before its BSS.  Initialised strings therefore
cannot simply be appended to the file: doing so would collide with fixed BSS
addresses.  This module pads through the BSS and the battle-message scratch
reservation, places translated records at RAM 0x8015C670, extends the loaded
PS-X EXE image, and rewrites only proven self-relative pointer fields.

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
UI_ARENA_FILE_OFFSET = 0x14CE70
UI_ARENA_RAM = PSX_FILE_TO_RAM_BIAS + UI_ARENA_FILE_OFFSET
HEAP_CEILING_RAM = 0x801F8000
MINIMUM_HEAP_BYTES = 0x60000

# The 91-record preview/conditions pool is the tail of root VM script 3.  It
# has no leaf pointers, so the complete script is copied to the arena and the
# sole authoritative root-table field is redirected.  The original block is
# deliberately retained for any non-authoritative incidental readers.
ROOT_RESOURCE_HEADER = 0xE14
ROOT_SCRIPT_ENTRY3_FIELD = 0xE24
ROOT_SCRIPT_ENTRY3_START = 0x1231
ROOT_SCRIPT_ENTRY3_END = 0x6A6C
ROOT_SCRIPT_ENTRY3_SHA256 = "9a339054071a07842f98462be5b62e9189f9b3e8b13c09b958627fe3624e1ca5"

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


def record_bytes(data: bytes | bytearray, start: int) -> bytes:
    end, _tokens = parse_renderer_record(data, start)
    return bytes(data[start:end])


def control_signature(raw: bytes) -> tuple[str, ...]:
    end, tokens = parse_renderer_record(raw, 0, len(raw))
    if end != len(raw):
        raise ValueError("bytes follow renderer record terminator")
    return tuple(token.raw.hex(" ").upper() for token in tokens if token.kind == "control")


def _int(value: Any, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"{field} must be an integer, got {value!r}")


def _verify_record_guard(executable: bytes | bytearray, row: dict[str, Any]) -> bytes:
    start = _int(row.get("source_offset", row.get("target")), "source_offset")
    raw = record_bytes(executable, start)
    expected_hex = row.get("raw_hex") or row.get("source_hex")
    if expected_hex and raw != bytes.fromhex(str(expected_hex)):
        raise ValueError(f"source bytes changed for record at 0x{start:X}")
    expected_hash = row.get("raw_sha256") or row.get("source_sha256")
    if expected_hash and sha256(raw) != str(expected_hash).lower():
        raise ValueError(f"source SHA-256 changed for record at 0x{start:X}")
    return raw


def _span_boundaries(raw: bytes) -> set[int]:
    _end, tokens = parse_renderer_record(raw, 0, len(raw))
    return {0, *(token.start for token in tokens), *(token.end for token in tokens)}


def apply_span_replacements(raw: bytes, replacements: list[dict[str, Any]], glyph_map: dict[str, int]) -> bytes:
    if not replacements:
        return raw
    boundaries = _span_boundaries(raw)
    source_controls = control_signature(raw)
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
        _dummy_end, tokens = parse_renderer_record(source + b"\xFF", 0)
        if any(token.kind == "control" for token in tokens):
            raise ValueError("replacement span contains a renderer control")
        output.extend(raw[cursor:start])
        output.extend(encode_ui_text(str(replacement["korean_text"]), glyph_map, terminate=False))
        cursor = end
    output.extend(raw[cursor:])
    rebuilt = bytes(output)
    if control_signature(rebuilt) != source_controls:
        raise ValueError("UI replacement changed renderer controls")
    return rebuilt


def rebuild_row_record(raw: bytes, row: dict[str, Any], glyph_map: dict[str, int]) -> bytes:
    replacements = row.get("replacements")
    if isinstance(replacements, list):
        return apply_span_replacements(raw, replacements, glyph_map)
    korean = row.get("korean_text")
    if korean is None:
        return raw
    if control_signature(raw) and not row.get("allow_full_record_rebuild"):
        raise ValueError("control-bearing row needs exact span replacements")
    rebuilt = encode_ui_text(str(korean), glyph_map, terminate=True)
    if not row.get("allow_control_change") and control_signature(raw) != control_signature(rebuilt):
        raise ValueError("full UI record rebuild changed controls")
    return rebuilt


@dataclass
class Arena:
    base: int = UI_ARENA_FILE_OFFSET
    data: bytearray = field(default_factory=bytearray)
    allocations: list[dict[str, Any]] = field(default_factory=list)
    interned: dict[bytes, int] = field(default_factory=dict)

    def align(self, alignment: int) -> None:
        wanted = align_up(self.base + len(self.data), alignment) - self.base
        if wanted > len(self.data):
            self.data.extend(b"\x00" * (wanted - len(self.data)))

    def add(self, raw: bytes, *, asset_id: str, key: str, intern: bool = True, alignment: int = 1) -> int:
        if intern and raw in self.interned:
            return self.interned[raw]
        self.align(alignment)
        offset = self.base + len(self.data)
        self.data.extend(raw)
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


def _patch_sequential_preview(
    executable: bytearray,
    arena: Arena,
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

    # Pool growth is safe only as the tail of a relocated copy of the whole VM
    # script.  All prefix bytecode stays at the same relative displacement and
    # the three alignment bytes before the next resource remain at the end.
    relocated_script = (
        bytes(executable[ROOT_SCRIPT_ENTRY3_START:pool_start])
        + bytes(rebuilt)
        + bytes(executable[pool_end:ROOT_SCRIPT_ENTRY3_END])
    )
    relocated_start = arena.add(
        relocated_script,
        asset_id=asset_id,
        key="root_script_3_with_preview_pool",
        intern=False,
        alignment=4,
    )
    patch_relative_pointer(executable, ROOT_SCRIPT_ENTRY3_FIELD, relocated_start)
    return {
        "asset_id": asset_id,
        "record_count": len(group["records"]),
        "translated_records": translated,
        "source_capacity": capacity,
        "rebuilt_bytes": len(rebuilt),
        "growth_bytes": len(rebuilt) - capacity,
        "root_script_source_start": ROOT_SCRIPT_ENTRY3_START,
        "root_script_source_end": ROOT_SCRIPT_ENTRY3_END,
        "root_script_relocated_start": relocated_start,
        "root_script_relocated_bytes": len(relocated_script),
        "root_pointer_field": ROOT_SCRIPT_ENTRY3_FIELD,
        "storage": "aggregate pool in relocated complete root VM script 3",
        "sha256": sha256(bytes(rebuilt)),
    }


def _patch_sequential_music_pool(
    executable: bytearray,
    arena: Arena,
    inventory: dict[str, Any],
    translations: dict[tuple[str, int], dict[str, Any]],
    glyph_map: dict[str, int],
) -> dict[str, Any]:
    group = inventory["common_music_demo_pool"]
    asset_id = str(group["asset_id"])
    arena.align(4)
    pool_start = arena.base + len(arena.data)
    old_to_new: dict[int, int] = {}
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
        target = arena.add(raw, asset_id=asset_id, key=f"record[{index}]", intern=False)
        old_to_new[source_offset] = target

    pointer_count = 0
    for source_row in group["records"]:
        target = old_to_new[_int(source_row["source_offset"], "source_offset")]
        for pointer_field in source_row.get("pointer_fields", []):
            field = _int(pointer_field, "pointer_field")
            expected_source = _int(source_row["source_offset"], "source_offset")
            if field + s32(executable, field) != expected_source:
                raise ValueError(
                    f"music/demo pointer 0x{field:X} no longer targets "
                    f"0x{expected_source:X}"
                )
            patch_relative_pointer(executable, field, target)
            pointer_count += 1
    if pointer_count != _int(group["nested_entry_count"], "nested_entry_count"):
        raise ValueError(
            f"music/demo pointer coverage {pointer_count} != {group['nested_entry_count']}"
        )
    return {
        "asset_id": asset_id,
        "record_count": len(group["records"]),
        "translated_records": translated,
        "pointer_count": pointer_count,
        "sequential_only_records": _int(group["sequential_only_record_count"], "sequential_only_record_count"),
        "arena_start": pool_start,
        "arena_end": arena.base + len(arena.data),
        "storage": "relocated contiguous pool",
    }


def _patch_pointer_group(
    executable: bytearray,
    arena: Arena,
    asset_id: str,
    source_rows: list[dict[str, Any]],
    translations: dict[tuple[str, int], dict[str, Any]],
    glyph_map: dict[str, int],
    *,
    require_all_targets: bool,
) -> dict[str, Any]:
    translated = 0
    pointers = 0
    output_by_source: dict[int, tuple[bytes, int]] = {}
    for source_row in source_rows:
        index = _int(source_row["entry_index"], "entry_index")
        overlay = translations.get((asset_id, index))
        required = bool(source_row.get("translation_target")) and require_all_targets
        if required and overlay is None:
            raise ValueError(f"missing table translation {asset_id}[{index}]")
        if overlay is None:
            continue
        source_offset = _int(source_row.get("source_offset", source_row.get("target")), "source_offset")
        source_raw = _verify_record_guard(executable, source_row)
        rebuilt = rebuild_row_record(source_raw, overlay, glyph_map)
        previous = output_by_source.get(source_offset)
        if previous is not None and previous[0] != rebuilt:
            raise ValueError(f"shared source record 0x{source_offset:X} has conflicting translations")
        if previous is None:
            target = arena.add(
                rebuilt,
                asset_id=asset_id,
                key=f"record[{index}]",
                intern=True,
            )
            output_by_source[source_offset] = (rebuilt, target)
        else:
            target = previous[1]
        field_value = overlay.get("pointer_field", source_row.get("pointer_field"))
        if field_value is None:
            raise ValueError(f"{asset_id}[{index}] has no pointer field")
        field = _int(field_value, "pointer_field")
        if field + s32(executable, field) != source_offset:
            raise ValueError(
                f"{asset_id}[{index}] pointer 0x{field:X} no longer targets "
                f"guarded source 0x{source_offset:X}"
            )
        patch_relative_pointer(executable, field, target)
        translated += 1
        pointers += 1
    return {
        "asset_id": asset_id,
        "source_entries": len(source_rows),
        "translated_entries": translated,
        "unique_rebuilt_records": len(output_by_source),
        "patched_pointers": pointers,
    }


def _inventory_table_map(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {
        str(table["asset_id"]): table
        for table in inventory["other_menu_visible_tables"]
    }
    result[str(inventory["second_ui_master"]["asset_id"])] = inventory["second_ui_master"]
    return result


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
    """Apply all approved SECOND menu overlays and extend SECOND.WAR."""

    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw.decode("utf-8"))
    documents = [load_json(Path(path)) for path in overlay_paths]
    translations = _translation_map(documents)

    executable_path = executable_root / SECOND_EXECUTABLE
    executable = bytearray(executable_path.read_bytes())
    if executable[:8] != PSX_EXE_MAGIC:
        raise ValueError("SECOND.WAR is not a PS-X EXE")
    if len(executable) != RETAIL_FILE_BYTES or u32(executable, 0x1C) != RETAIL_T_SIZE:
        raise ValueError("SECOND.WAR must be unextended before UI relocation")
    if UI_ARENA_FILE_OFFSET <= len(executable):
        raise AssertionError("UI arena does not follow the retail executable")

    arena = Arena()
    groups: list[dict[str, Any]] = []
    groups.append(
        _patch_sequential_preview(executable, arena, inventory, translations, glyph_map)
    )
    groups.append(
        _patch_sequential_music_pool(executable, arena, inventory, translations, glyph_map)
    )

    table_map = _inventory_table_map(inventory)
    # Pointer-backed assets present in the inventory.  Non-target structural
    # entries remain pointed at their pristine records.
    for asset_id, table in table_map.items():
        groups.append(
            _patch_pointer_group(
                executable,
                arena,
                asset_id,
                list(table["records"]),
                translations,
                glyph_map,
                require_all_targets=True,
            )
        )

    # Overlay-only pointer groups cover the few common master records that are
    # not part of the inventory's main table list (command and option labels).
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
    for asset_id, rows in sorted(overlay_only.items()):
        groups.append(
            _patch_pointer_group(
                executable,
                arena,
                asset_id,
                rows,
                translations,
                glyph_map,
                require_all_targets=False,
            )
        )

    groups.append(_patch_common_audio_option_width(executable))

    if not arena.data:
        raise ValueError("UI arena is unexpectedly empty")
    executable.extend(b"\x00" * (UI_ARENA_FILE_OFFSET - len(executable)))
    if len(executable) != UI_ARENA_FILE_OFFSET:
        raise AssertionError("BSS padding did not end at the UI arena")
    executable.extend(arena.data)
    new_length = align_up(len(executable), PSX_HEADER_BYTES)
    executable.extend(b"\x00" * (new_length - len(executable)))
    struct.pack_into("<I", executable, 0x1C, new_length - PSX_HEADER_BYTES)
    loaded_end = PSX_FILE_TO_RAM_BIAS + new_length
    heap_bytes_after_ui = HEAP_CEILING_RAM - (loaded_end + 4)
    if heap_bytes_after_ui < MINIMUM_HEAP_BYTES:
        raise ValueError(
            f"UI arena leaves only 0x{heap_bytes_after_ui:X} heap bytes; "
            f"minimum is 0x{MINIMUM_HEAP_BYTES:X}"
        )
    if len(executable) != u32(executable, 0x1C) + PSX_HEADER_BYTES:
        raise AssertionError("PS-X EXE t_size/file-size mismatch")
    if PSX_FILE_TO_RAM_BIAS + UI_ARENA_FILE_OFFSET != UI_ARENA_RAM:
        raise AssertionError("UI arena file/RAM mapping changed")

    executable_path.write_bytes(executable)
    return {
        "format": "srwcb-second-executable-ui-relocation-v1",
        "path": str(SECOND_EXECUTABLE).replace("\\", "/"),
        "inventory": {
            "path": str(inventory_path),
            "sha256": sha256(inventory_raw),
        },
        "overlays": [
            {"path": str(path), "sha256": sha256(Path(path).read_bytes())}
            for path in overlay_paths
        ],
        "arena_file_start": UI_ARENA_FILE_OFFSET,
        "arena_ram_start": UI_ARENA_RAM,
        "arena_payload_bytes": len(arena.data),
        "arena_file_end_unaligned": UI_ARENA_FILE_OFFSET + len(arena.data),
        "new_file_bytes": new_length,
        "new_t_size": new_length - PSX_HEADER_BYTES,
        "new_loaded_end_ram": loaded_end,
        "heap_bytes_after_ui_and_guard": heap_bytes_after_ui,
        "minimum_heap_bytes": MINIMUM_HEAP_BYTES,
        "groups": groups,
        "allocations": arena.allocations,
        "executable_sha256_before_runtime_boundary_patch": sha256(bytes(executable)),
    }


__all__ = [
    "collect_korean_ui_texts",
    "patch_second_executable_ui",
    "encode_ui_text",
    "parse_renderer_record",
    "control_signature",
    "UI_ARENA_FILE_OFFSET",
    "UI_ARENA_RAM",
]
