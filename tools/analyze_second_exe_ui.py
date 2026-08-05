#!/usr/bin/env python3
"""Prove the executable-resident common UI string structures in SRWCB.

This analyzer is deliberately read-only with respect to the five PS-X EXEs.
It validates three known common assets using exact binary comparison, the
MIPS code that resolves self-relative pointers, and all pointer fields that
lead to the strings.  The JSON report is suitable as a guard input for a
future patcher; heuristic executable-wide string candidates are not used.
"""

from __future__ import annotations

# --- 이식용 부트스트랩 (자동 삽입) ---
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.exists(_os.path.join(_d, "srwcb_paths.py")):
    _d = _os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import srwcb_paths as _P
_P.ensure_dirs()
if str(_P.TOOLS) not in _sys.path:
    _sys.path.insert(0, str(_P.TOOLS))
# ------------------------------------


import argparse
import hashlib
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from capstone import CS_ARCH_MIPS, CS_MODE_LITTLE_ENDIAN, CS_MODE_MIPS32, Cs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_dialogue_candidates import (  # noqa: E402
    iter_encoded_records,
    load_glyph_mapping,
    render_japanese,
)


WORKSPACE = _P.WORK
DEFAULT_EXTRACTED = _P.EXTRACTED
DEFAULT_GLYPH_MAP = _P.FONT_MAPPING
DEFAULT_OUTPUT = (
    (_P.WORK / "research") / "second_exe_ui_reference_report.json"
)

LOOKUP_CANONICAL_OFFSET = 0x59894
LOOKUP_SIGNATURE_SIZE = 0x3C
TITLE_COUNT = 84
TITLE_NESTED_FIRST_INDEX = 88
INTERMISSION_MASTER_INDEX = 31
PREVIEW_HEADER_BYTES = 0x46


@dataclass(frozen=True)
class Layout:
    path: str
    sha256: str
    lookup: int
    root_header: int
    root_initializer: int
    preview: int
    preview_end: int
    title_start: int
    title_end: int
    title_pointer_first: int
    title_master_header: int
    intermission_start: int
    intermission_end: int
    intermission_pointer: int
    intermission_master_header: int


LAYOUTS: tuple[Layout, ...] = (
    Layout(
        "SLPS_020.70",
        "a64e4b61f3a9fa11527dcd7abb5a6659eb70321d89c6d8d55d64b869fb4ddfa1",
        0x47478,
        0xD48,
        0x4750C,
        0x5D1C,
        0x6A08,
        0xBE6F,
        0xC2B6,
        0xA0E5,
        0x9710,
        0x1C1E1,
        0x1C271,
        0x1A1D4,
        0x1A154,
    ),
    Layout(
        "TR.WAR",
        "eca38375db88111a779325e77bbf84ea44b247910e350ecd70b72fc34473a9cd",
        0x53020,
        0xDF8,
        0x530B4,
        0x5DE6,
        0x6AD0,
        0xB864,
        0xBCAB,
        0xA051,
        0x967C,
        0x1A949,
        0x1A9D9,
        0x1893C,
        0x188BC,
    ),
    Layout(
        "EX/EX.WAR",
        "4bbd7078e991b2790c60af20c1c7301c0b761efd1e6af9d9da643611602ec350",
        0x53044,
        0xDF8,
        0x530D8,
        0x5E16,
        0x6B00,
        0xB86C,
        0xBCB3,
        0xA055,
        0x9680,
        0x1A950,
        0x1A9E0,
        0x18944,
        0x188C4,
    ),
    Layout(
        "SECOND/SECOND.WAR",
        "a18915d940e69d7554631995c40f8de97b84c78fc164a90d667c8543c75acf3c",
        0x59894,
        0xE14,
        0x59928,
        0x5D80,
        0x6A6C,
        0xB3E8,
        0xB82F,
        0x9BD1,
        0x91FC,
        0x261DD,
        0x2626D,
        0x243A0,
        0x24320,
    ),
    Layout(
        "THIRD/THIRD.WAR",
        "71ce2de0b282f6439f23bef053d5b0c1f6708a7f88fce8487570444896e91545",
        0x59F44,
        0xE14,
        0x59FD8,
        0x5D80,
        0x6A6C,
        0xB6C4,
        0xBB0B,
        0x9EAD,
        0x94D8,
        0x26689,
        0x26719,
        0x2484C,
        0x247CC,
    ),
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def s32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def psx_base(data: bytes) -> int:
    if data[:8] != b"PS-X EXE":
        raise ValueError("not a PS-X EXE")
    return u32(data, 0x18) - 0x800


def rel_target(base: int, field: int, relative: int) -> int:
    """Return a file offset for the game's field-relative 32-bit pointer."""

    address = (base + field + relative) & 0xFFFFFFFF
    return address - base


def find_relative_references(
    data: bytes, base: int, targets: Iterable[int]
) -> dict[int, list[int]]:
    target_set = set(targets)
    found = {target: [] for target in target_set}
    # Tables nested in the UI resource are intentionally unaligned, hence the
    # byte-by-byte scan.  This is a reference scan, not a string scan.
    for field in range(0x800, len(data) - 3):
        target = rel_target(base, field, s32(data, field))
        if target in target_set:
            found[target].append(field)
    return found


def find_aligned_absolute_references(
    data: bytes, base: int, targets: Iterable[int]
) -> dict[int, list[int]]:
    wanted = {base + target: target for target in targets}
    found = {target: [] for target in targets}
    for field in range(0x800, len(data) - 3, 4):
        value = u32(data, field)
        if value in wanted:
            found[wanted[value]].append(field)
    return found


def disassemble(data: bytes, base: int, offset: int, size: int) -> list[str]:
    engine = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    return [
        f"0x{insn.address:08X}: {insn.mnemonic} {insn.op_str}".rstrip()
        for insn in engine.disasm(data[offset : offset + size], base + offset)
    ]


def jal_target(pc: int, word: int) -> int:
    return ((pc + 4) & 0xF0000000) | ((word & 0x03FFFFFF) << 2)


def adjacent_table_load_calls(
    data: bytes,
    base: int,
    lookup_offset: int,
    header_offset: int,
    *,
    required_index: int | None = None,
) -> list[int]:
    """Find `lui a0; lw a0,header; jal lookup` call sites."""

    lookup_address = base + lookup_offset
    hits: list[int] = []
    for offset in range(0x800, len(data) - 16, 4):
        first, second, third, delay = struct.unpack_from("<IIII", data, offset)
        if first >> 26 != 0x0F or (first >> 16) & 31 != 4:  # lui a0
            continue
        if (
            second >> 26 != 0x23
            or (second >> 21) & 31 != 4
            or (second >> 16) & 31 != 4
        ):  # lw a0, imm(a0)
            continue
        if third >> 26 != 3 or jal_target(base + offset + 8, third) != lookup_address:
            continue
        low = second & 0xFFFF
        if low & 0x8000:
            low -= 0x10000
        effective = (((first & 0xFFFF) << 16) + low) & 0xFFFFFFFF
        if effective != base + header_offset:
            continue
        if required_index is not None:
            if (
                delay >> 26 != 9
                or (delay >> 21) & 31 != 0
                or (delay >> 16) & 31 != 5
                or delay & 0xFFFF != required_index
            ):
                continue
        hits.append(offset)
    return hits


def verify_lookup_routine(data: bytes, layout: Layout, canonical: bytes) -> None:
    actual = data[layout.lookup : layout.lookup + len(canonical)]
    if actual != canonical:
        raise ValueError(f"{layout.path}: lookup routine signature mismatch")
    instructions = disassemble(data, psx_base(data), layout.lookup, len(canonical))
    expected = ["andi", "sll", "addu", "lbu", "lbu", "lbu"]
    actual_names = [line.split(": ", 1)[1].split(" ", 1)[0] for line in instructions]
    if actual_names[: len(expected)] != expected:
        raise ValueError(f"{layout.path}: lookup routine disassembly mismatch")
    if not instructions[-1].endswith("addu $v0, $a0, $v0"):
        raise ValueError(f"{layout.path}: lookup return expression mismatch")


def verify_root_initializer(data: bytes, base: int, layout: Layout) -> None:
    words = [u32(data, layout.root_initializer + index * 4) for index in range(12)]
    first, second = words[:2]
    if first >> 26 != 0x0F or (first >> 16) & 31 != 2:
        raise ValueError(f"{layout.path}: root initializer has no `lui v0`")
    if (
        second >> 26 != 0x23
        or (second >> 21) & 31 != 2
        or (second >> 16) & 31 != 2
    ):
        raise ValueError(f"{layout.path}: root initializer has no `lw v0`")
    low = second & 0xFFFF
    if low & 0x8000:
        low -= 0x10000
    effective = (((first & 0xFFFF) << 16) + low) & 0xFFFFFFFF
    if effective != base + layout.root_header:
        raise ValueError(f"{layout.path}: root initializer points elsewhere")

    # The following code is the inline self-relative lookup used to select a
    # root script: field = base + index*4; result = field + *field.
    canonical = [
        0x00042080,  # sll v? -- checked with masks below where needed
    ]
    del canonical  # The Capstone assertions below are clearer than raw masks.
    names = [
        line.split(": ", 1)[1].split(" ", 1)[0]
        for line in disassemble(data, base, layout.root_initializer, 0x30)
    ]
    if names[:8] != ["lui", "lw", "sll", "addu", "lw", "nop", "addu", "addu"]:
        raise ValueError(f"{layout.path}: root relative-lookup sequence mismatch")


def decoded_records(data: bytes, start: int, end: int, mapping: Any) -> list[Any]:
    records = list(iter_encoded_records(data, start, end))
    if not records or records[-1].end > end:
        raise ValueError(f"invalid FF record interval 0x{start:X}-0x{end:X}")
    return records


def record_json(record: Any, mapping: Any, *, pointer_field: int | None = None) -> dict[str, Any]:
    rendered = render_japanese(record, mapping, "reviewed")
    result: dict[str, Any] = {
        "offset": record.start,
        "offset_hex": f"0x{record.start:X}",
        "end_offset_exclusive": record.end,
        "byte_length": record.end - record.start,
        "raw_sha256": None,
        "japanese_text": rendered["japanese_text"],
    }
    # The caller fills raw_sha256 because ParsedRecord does not retain source.
    if pointer_field is not None:
        result["pointer_field"] = pointer_field
        result["pointer_field_hex"] = f"0x{pointer_field:X}"
    return result


def make_report(extracted: Path, glyph_map: Path) -> dict[str, Any]:
    mapping = load_glyph_mapping(glyph_map)
    source: dict[str, bytes] = {}
    for layout in LAYOUTS:
        data = (extracted / Path(layout.path)).read_bytes()
        actual_sha = sha256(data)
        if actual_sha != layout.sha256:
            raise ValueError(
                f"{layout.path}: expected SHA-256 {layout.sha256}, got {actual_sha}"
            )
        source[layout.path] = data

    canonical_layout = next(item for item in LAYOUTS if item.path == "SECOND/SECOND.WAR")
    canonical_data = source[canonical_layout.path]
    lookup_signature = canonical_data[
        LOOKUP_CANONICAL_OFFSET : LOOKUP_CANONICAL_OFFSET + LOOKUP_SIGNATURE_SIZE
    ]

    canonical_preview_records = decoded_records(
        canonical_data,
        canonical_layout.preview + PREVIEW_HEADER_BYTES,
        canonical_layout.preview_end,
        mapping,
    )
    canonical_title_records = decoded_records(
        canonical_data,
        canonical_layout.title_start,
        canonical_layout.title_end + 1,
        mapping,
    )
    # title_end is the byte after the final FF in the layout table.
    canonical_title_records = [
        record for record in canonical_title_records if record.start < canonical_layout.title_end
    ]
    canonical_intermission_records = decoded_records(
        canonical_data,
        canonical_layout.intermission_start,
        canonical_layout.intermission_end,
        mapping,
    )
    if len(canonical_preview_records) != 91:
        raise ValueError("canonical preview pool is not 91 FF records")
    if len(canonical_title_records) != TITLE_COUNT:
        raise ValueError("canonical title table is not 84 FF records")
    if len(canonical_intermission_records) != 1:
        raise ValueError("canonical intermission entry is not one FF record")

    preview_raw = canonical_data[
        canonical_layout.preview + PREVIEW_HEADER_BYTES : canonical_preview_records[-1].end
    ]
    title_raw = canonical_data[canonical_layout.title_start : canonical_layout.title_end]
    intermission_raw = canonical_data[
        canonical_layout.intermission_start : canonical_layout.intermission_end
    ]

    executable_reports: dict[str, Any] = {}
    preview_hashes: set[str] = set()
    title_hashes: set[str] = set()
    intermission_hashes: set[str] = set()

    for layout in LAYOUTS:
        data = source[layout.path]
        base = psx_base(data)
        verify_lookup_routine(data, layout, lookup_signature)
        verify_root_initializer(data, base, layout)

        if u32(data, layout.root_header) != base + layout.root_header + 4:
            raise ValueError(f"{layout.path}: invalid root table absolute base")
        if u32(data, layout.title_master_header) != base + layout.title_master_header + 4:
            raise ValueError(f"{layout.path}: invalid title master absolute base")
        if (
            u32(data, layout.intermission_master_header)
            != base + layout.intermission_master_header + 4
        ):
            raise ValueError(f"{layout.path}: invalid intermission master absolute base")

        preview_records = decoded_records(
            data, layout.preview + PREVIEW_HEADER_BYTES, layout.preview_end, mapping
        )
        if len(preview_records) != 91:
            raise ValueError(f"{layout.path}: preview record count changed")
        preview_bytes = data[layout.preview + PREVIEW_HEADER_BYTES : preview_records[-1].end]
        if preview_bytes != preview_raw:
            raise ValueError(f"{layout.path}: preview pool differs from canonical")
        preview_hashes.add(sha256(preview_bytes))

        title_records = decoded_records(data, layout.title_start, layout.title_end + 1, mapping)
        title_records = [record for record in title_records if record.start < layout.title_end]
        if len(title_records) != TITLE_COUNT:
            raise ValueError(f"{layout.path}: title record count changed")
        title_bytes = data[layout.title_start : layout.title_end]
        if title_bytes != title_raw:
            raise ValueError(f"{layout.path}: title pool differs from canonical")
        title_hashes.add(sha256(title_bytes))

        title_starts = [record.start for record in title_records]
        title_refs = find_relative_references(data, base, title_starts)
        title_extra_arithmetic_matches: list[dict[str, int]] = []
        for index, record in enumerate(title_records):
            expected_field = layout.title_pointer_first + index * 4
            if expected_field not in title_refs[record.start]:
                raise ValueError(
                    f"{layout.path}: title {index} is missing its authoritative pointer"
                )
            if rel_target(base, expected_field, s32(data, expected_field)) != record.start:
                raise ValueError(f"{layout.path}: title {index} pointer mismatch")
            for field in title_refs[record.start]:
                if field != expected_field:
                    # An executable-wide byte-phase scan can produce accidental
                    # arithmetic matches in font/code bytes.  They are reported,
                    # but only the 172-entry table selected by master[36] is a
                    # code-referenced pointer structure.
                    title_extra_arithmetic_matches.append(
                        {"record_index": index, "field_offset": field}
                    )

        nested_base = layout.title_pointer_first - TITLE_NESTED_FIRST_INDEX * 4
        master_base = layout.title_master_header + 4
        master_field_36 = master_base + 36 * 4
        if rel_target(base, master_field_36, s32(data, master_field_36)) != nested_base:
            raise ValueError(f"{layout.path}: master[36] does not select title table")
        if nested_base + TITLE_NESTED_FIRST_INDEX * 4 != layout.title_pointer_first:
            raise AssertionError("title nested-index arithmetic failed")

        title_code_calls = adjacent_table_load_calls(
            data,
            base,
            layout.lookup,
            layout.title_master_header,
            required_index=36,
        )
        if not title_code_calls:
            raise ValueError(f"{layout.path}: no MIPS code reference to title master[36]")

        intermission_records = decoded_records(
            data, layout.intermission_start, layout.intermission_end, mapping
        )
        if len(intermission_records) != 1:
            raise ValueError(f"{layout.path}: intermission record count changed")
        intermission_bytes = data[layout.intermission_start : layout.intermission_end]
        if intermission_bytes != intermission_raw:
            raise ValueError(f"{layout.path}: intermission record differs from canonical")
        intermission_hashes.add(sha256(intermission_bytes))
        inter_refs = find_relative_references(data, base, [layout.intermission_start])
        if inter_refs[layout.intermission_start] != [layout.intermission_pointer]:
            raise ValueError(f"{layout.path}: intermission entry pointer is not exclusive")
        intermission_base = layout.intermission_master_header + 4
        expected_inter_field = intermission_base + INTERMISSION_MASTER_INDEX * 4
        if expected_inter_field != layout.intermission_pointer:
            raise ValueError(f"{layout.path}: intermission pointer is not master[31]")

        intermission_code_calls = adjacent_table_load_calls(
            data,
            base,
            layout.lookup,
            layout.intermission_master_header,
        )
        if layout.path != "SLPS_020.70" and not intermission_code_calls:
            raise ValueError(f"{layout.path}: missing game-side intermission table loads")

        preview_starts = [record.start for record in preview_records]
        preview_rel_refs = find_relative_references(data, base, preview_starts)
        preview_abs_refs = find_aligned_absolute_references(data, base, preview_starts)
        if any(preview_abs_refs.values()):
            raise ValueError(f"{layout.path}: preview unexpectedly has absolute leaf pointers")
        preview_arithmetic_matches = [
            {"record_offset": target, "field_offset": field}
            for target, fields in preview_rel_refs.items()
            for field in fields
        ]

        executable_reports[layout.path] = {
            "sha256": layout.sha256,
            "psx_file_to_ram_base": f"0x{base:08X}",
            "lookup_routine": {
                "file_offset": layout.lookup,
                "ram_address": f"0x{base + layout.lookup:08X}",
                "instructions": disassemble(data, base, layout.lookup, LOOKUP_SIGNATURE_SIZE),
            },
            "root_resource": {
                "header_offset": layout.root_header,
                "initializer_offset": layout.root_initializer,
                "initializer_instructions": disassemble(
                    data, base, layout.root_initializer, 0x30
                ),
            },
            "preview_pool": {
                "common_block_offset": layout.preview,
                "record_pool_offset": layout.preview + PREVIEW_HEADER_BYTES,
                "record_pool_end": preview_records[-1].end,
                "following_resource_header": layout.preview_end,
                "record_count": len(preview_records),
                "authoritative_relative_leaf_reference_count": 0,
                "aligned_absolute_leaf_reference_count": 0,
                "non_table_arithmetic_matches": preview_arithmetic_matches,
            },
            "music_menu_titles": {
                "master_header": layout.title_master_header,
                "master_entry": 36,
                "nested_table": nested_base,
                "first_nested_index": TITLE_NESTED_FIRST_INDEX,
                "first_pointer_field": layout.title_pointer_first,
                "record_pool_start": layout.title_start,
                "record_pool_end": layout.title_end,
                "code_call_sites_master_36": title_code_calls,
                "non_table_arithmetic_matches": title_extra_arithmetic_matches,
            },
            "intermission_menu": {
                "master_header": layout.intermission_master_header,
                "master_entry": INTERMISSION_MASTER_INDEX,
                "pointer_field": layout.intermission_pointer,
                "record_start": layout.intermission_start,
                "record_end": layout.intermission_end,
                "code_table_lookup_sites": intermission_code_calls,
            },
        }

    if len(preview_hashes) != 1 or len(title_hashes) != 1 or len(intermission_hashes) != 1:
        raise AssertionError("the five common copies are not byte-identical")

    title_items: list[dict[str, Any]] = []
    for index, record in enumerate(canonical_title_records):
        field = canonical_layout.title_pointer_first + index * 4
        item = record_json(record, mapping, pointer_field=field)
        item["raw_sha256"] = sha256(canonical_data[record.start : record.end])
        item["nested_index"] = TITLE_NESTED_FIRST_INDEX + index
        item["stored_relative"] = s32(canonical_data, field)
        title_items.append(item)

    preview_items: list[dict[str, Any]] = []
    for index, record in enumerate(canonical_preview_records):
        item = record_json(record, mapping)
        item["raw_sha256"] = sha256(canonical_data[record.start : record.end])
        item["sequential_index"] = index
        preview_items.append(item)

    intermission_record = canonical_intermission_records[0]
    intermission_item = record_json(
        intermission_record,
        mapping,
        pointer_field=canonical_layout.intermission_pointer,
    )
    intermission_item["raw_sha256"] = sha256(
        canonical_data[intermission_record.start : intermission_record.end]
    )
    intermission_item["master_index"] = INTERMISSION_MASTER_INDEX

    return {
        "format": "srwcb-second-executable-ui-reference-audit-v1",
        "policy": {
            "heuristic_string_scan_used": False,
            "binary_files_modified": False,
            "reference_requirements": [
                "byte-identical in all five PS-X EXEs",
                "MIPS lookup routine verified with Capstone",
                "exact exclusive self-relative leaf references where present",
            ],
        },
        "glyph_mapping_sha256": mapping.file_sha256,
        "lookup_semantics": {
            "formula": "target = pointer_field_address + signed_le32(pointer_field)",
            "canonical_second_file_offset": LOOKUP_CANONICAL_OFFSET,
            "canonical_signature_sha256": sha256(lookup_signature),
        },
        "common_copy_hashes": {
            "preview_91_record_pool": next(iter(preview_hashes)),
            "music_menu_84_record_pool": next(iter(title_hashes)),
            "intermission_menu_record": next(iter(intermission_hashes)),
        },
        "executables": executable_reports,
        "safe_assets": {
            "music_menu_titles": {
                "classification": "authoritative_self_relative_pointer_records",
                "record_count": TITLE_COUNT,
                "canonical_pool_bytes": len(title_raw),
                "relocation": (
                    "Repack complete FF-terminated records into an approved arena and "
                    "rewrite each field as new_record_offset - pointer_field_offset."
                ),
                "records": title_items,
            },
            "intermission_menu": {
                "classification": "exclusive_master_entry_31_ff_record",
                "record_count": 1,
                "canonical_bytes": len(intermission_raw),
                "relocation": (
                    "Relocate the whole control-bearing FF record and rewrite master[31]; "
                    "never patch its visible menu lines as independent slots."
                ),
                "menu_labels": [
                    "インタ-ミッション",
                    "デ-タセ-ブ",
                    "ユニット改造",
                    "武器改造",
                    "ユニット能力",
                    "パイロット能力",
                    "のりかえ",
                    "強化パ-ツ",
                    "次のマップへ",
                    "総タ-ン数",
                    "資金",
                ],
                "record": intermission_item,
            },
            "preview_dialogue_pool": {
                "classification": "sequential_ff_records_without_leaf_pointers",
                "record_count": len(preview_items),
                "canonical_pool_bytes": len(preview_raw),
                "relocation": (
                    "Only aggregate in-place repacking is currently approved: preserve the "
                    "pool start and following resource header, concatenate all 91 translated "
                    "FF records, and fail if the 3235-byte pool overflows."
                ),
                "independent_record_relocation_approved": False,
                "records": preview_items,
            },
        },
        "patch_guardrails": {
            "no_fixed_slot_truncation": True,
            "music_and_intermission_leaf_arena_bytes": len(title_raw) + len(intermission_raw),
            "preview_aggregate_arena_bytes": len(preview_raw),
            "overflow_policy": (
                "Abort. Appending to a PS-X EXE is not approved until the loaded-image/BSS "
                "collision boundary is independently proven."
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extracted", type=Path, default=DEFAULT_EXTRACTED)
    parser.add_argument("--glyph-map", type=Path, default=DEFAULT_GLYPH_MAP)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate and print the summary without writing the JSON report",
    )
    args = parser.parse_args()

    report = make_report(args.extracted, args.glyph_map)
    if not args.check_only:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"wrote {args.output}")
    print("verified 5 executable copies")
    print("music/menu titles: 84 exclusive relative-pointer records")
    print("intermission menu: master[31], one exclusive FF record")
    print("preview dialogue: 91 sequential FF records, aggregate repack only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
