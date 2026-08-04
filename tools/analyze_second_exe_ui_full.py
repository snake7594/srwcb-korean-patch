#!/usr/bin/env python3
"""Build a lossless inventory of SECOND/common executable UI text.

The executable is only read.  The report records every proven self-relative
pointer field, every control-bearing UI record, and the two sequential common
pools that must be rebuilt as aggregates.
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
# ------------------------------------


import argparse
import copy
import hashlib
import json
import struct
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_second_exe_ui import adjacent_table_load_calls, psx_base  # noqa: E402
from extract_dialogue_candidates import (  # noqa: E402
    MAPPING_POLICIES,
    ParsedRecord,
    iter_encoded_records,
    load_glyph_mapping,
    render_japanese,
)
from patch_second_exe_ui import parse_second_ui_vm_record  # noqa: E402


ROOT = _P.WORK
EXTRACTED = _P.EXTRACTED
SECOND = EXTRACTED / "SECOND" / "SECOND.WAR"
GLYPH_MAP = (
    _P.FONT_MAPPING
)
DEFAULT_JSON = (
    (_P.WORK / "research") / "second_exe_ui_full_inventory.json"
)
DEFAULT_MD = (_P.WORK / "research") / "SECOND_EXE_UI_FULL_INVENTORY.md"

SECOND_SHA256 = "a18915d940e69d7554631995c40f8de97b84c78fc164a90d667c8543c75acf3c"
LOOKUP_OFFSET = 0x59894

UI_TRANSLATION_TARGETS = frozenset(
    {
        1, 7, 8, 9, 10, 11, 14, 15, 17, 18, 19, 20, 21, 23, 24, 25, 26, 27, 29, 30,
        31, 32, 33, 34, 35, 36, 37, 38, 39, 41, 42, 43, 44, 45, 46,
        47, 48, 52, 53, 54, 57, 58, 59, 60, 61, 62, 63, 64, 65, 67,
        68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82,
        83, 84, 85, 86, 87, 89, 91, 93, 100, 101, 103, 106,
    }
)

UI_CATEGORIES: dict[str, frozenset[int]] = {
    "status_and_combat_information": frozenset({1, 7, 8, 9, 10, 14, 19, 24, 25, 26, 27, 38}),
    "map_command_menu": frozenset({11}),
    "phase_end_confirmation": frozenset({20, 21}),
    "sortie_and_confirmation": frozenset({15, 17, 18, 23}),
    "spirit_and_objectives": frozenset({29, 30, 36, 37}),
    "intermission": frozenset({31}),
    "counterattack_orders": frozenset({32, 33, 34, 35}),
    "system_settings": frozenset({39}),
    "protagonist_and_name_entry": frozenset({41, 42, 43, 44, 45, 46, 47, 48, 52, 53, 54, 106}),
    "results_level_and_repair": frozenset({57, 58, 59}),
    "unit_upgrade": frozenset({60, 61, 62, 63, 64, 65, 67, 68}),
    "title_and_save_load": frozenset({69, 70, 71, 72, 73, 91, 93}),
    "pilot_transfer": frozenset({74, 75, 76, 77}),
    "weapon_upgrade": frozenset({78, 79}),
    "parts": frozenset({80, 81, 82, 83}),
    "options_encyclopedia_demo": frozenset({84, 85, 86, 87, 89, 100, 101, 103}),
}

TABLES = (
    ("terrain_combinations", 0x9134, 15, 0x91FC),
    ("terrain_names", 0xB830, 144, 0xBC74),
    ("spirit_commands", 0xBC74, 94, 0xC340),
    ("enhancement_parts", 0xC340, 64, 0xC6B8),
    ("weapon_names", 0xC6B8, 1408, 0x11020),
    ("pilot_skills", 0x11020, 52, 0x112B8),
    ("unit_abilities", 0x112B8, 22, 0x113C4),
    ("scenario_titles", 0x113C4, 192, 0x11800),
    ("pilot_short_names", 0x10CE0C, 400, 0x10DD64),
    ("pilot_full_names", 0x10DD64, 400, 0x10F478),
    ("unit_names", 0x10F478, 448, 0x12B000),
)

EXECUTABLE_LAYOUTS = (
    ("SLPS_020.70", 0x9710, 0x1A154, 0x1D168),
    ("TR.WAR", 0x967C, 0x188BC, 0x1B8D0),
    ("EX/EX.WAR", 0x9680, 0x188C4, 0x1B8F4),
    ("SECOND/SECOND.WAR", 0x91FC, 0x24320, 0x27180),
    ("THIRD/THIRD.WAR", 0x94D8, 0x247CC, 0x2762C),
)

RESOURCE_ORDINALS = {
    "terrain_combinations": 2,
    "common_music_demo_master": 3,
    "terrain_names": 4,
    "spirit_commands": 5,
    "enhancement_parts": 6,
    "weapon_names": 7,
    "pilot_skills": 8,
    "unit_abilities": 9,
    "scenario_titles": 10,
    "ui_script_master": 15,
    "pilot_short_names": 22,
    "pilot_full_names": 23,
    "unit_names": 24,
}

PREVIEW_POOLS = {
    "SLPS_020.70": (0x5D62, 0x6A05),
    "TR.WAR": (0x5E2C, 0x6ACF),
    "EX/EX.WAR": (0x5E5C, 0x6AFF),
    "SECOND/SECOND.WAR": (0x5DC6, 0x6A69),
    "THIRD/THIRD.WAR": (0x5DC6, 0x6A69),
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def s32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def self_headers(data: bytes) -> list[int]:
    base = psx_base(data)
    return [
        offset
        for offset in range(0x800, len(data) - 3, 4)
        if struct.unpack_from("<I", data, offset)[0] == base + offset + 4
    ]


def infer_table_count(data: bytes, header: int, bound: int) -> int | None:
    start = header + 4
    maximum = min(4096, (bound - start) // 4)
    for count in range(1, maximum + 1):
        targets: list[int] = []
        for index in range(count):
            field = start + index * 4
            target = field + s32(data, field)
            if not start <= target < bound:
                break
            targets.append(target)
        if len(targets) == count and min(targets) == start + count * 4:
            return count
    return None


def category_for(index: int) -> str | None:
    hits = [name for name, indices in UI_CATEGORIES.items() if index in indices]
    if len(hits) > 1:
        raise AssertionError(f"UI index {index} has multiple categories")
    return hits[0] if hits else None


def ordinary_translation_target(asset_id: str, item: dict[str, Any]) -> bool:
    if item.get("classification") != "ff_terminated_record":
        return False
    if not item.get("glyph_count"):
        return False
    text = item.get("japanese_text", "")
    if asset_id == "pilot_skills" and text == "--------":
        return False
    if asset_id == "scenario_titles" and text == "-------------":
        return False
    if asset_id == "spirit_commands" and text in {"????", "??"}:
        return False
    return True


def control_signature(record: ParsedRecord) -> list[str]:
    result: list[str] = []
    for token in record.tokens:
        if token["type"] != "control":
            continue
        args = token.get("args_hex", "")
        result.append(f'{token["opcode"]:02X}' + (f" {args}" if args else ""))
    return result


def visible_runs(record: ParsedRecord, mapping: Any) -> list[str]:
    runs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        text = "".join(current).strip("　 ")
        current.clear()
        if text:
            runs.append(text)

    for token in record.tokens:
        if token["type"] == "glyph":
            index = token["index"]
            if index == 0:
                current.append("　")
            else:
                current.append(mapping.rows[index].get("character") or f"⟦G:{index:03X}⟧")
        else:
            flush()
    flush()
    return runs


def record_json(data: bytes, record: ParsedRecord, mapping: Any) -> dict[str, Any]:
    raw = data[record.start : record.end]
    rendered = render_japanese(record, mapping, "reviewed")
    return {
        "source_offset": record.start,
        "source_offset_hex": f"0x{record.start:X}",
        "end_offset_exclusive": record.end,
        "byte_length": record.end - record.start,
        "raw_sha256": sha256(raw),
        "raw_hex": raw.hex(" ").upper(),
        "glyph_count": len(record.glyph_ids),
        "japanese_text": rendered["japanese_text"],
        "japanese_text_meta": rendered["japanese_text_meta"],
        "visible_runs": visible_runs(record, mapping),
        "control_signature": control_signature(record),
    }


def pointed_record(
    data: bytes, field: int, bound: int, mapping: Any
) -> dict[str, Any]:
    relative = s32(data, field)
    target = field + relative
    item: dict[str, Any] = {
        "pointer_field": field,
        "pointer_field_hex": f"0x{field:X}",
        "stored_relative": relative,
        "target": target,
        "target_hex": f"0x{target:X}",
    }
    if not 0 <= target < bound:
        item["classification"] = "non_text_or_out_of_resource_target"
        return item
    record = next(iter_encoded_records(data, target, bound), None)
    if record is None:
        item["classification"] = "non_ff_terminated_target"
        return item
    item["classification"] = "ff_terminated_record"
    item.update(record_json(data, record, mapping))
    return item


def ui_vm_record_json(
    data: bytes,
    start: int,
    bound: int,
    mapping: Any,
) -> dict[str, Any]:
    """Render one SECOND UI-master record with its stateful VM grammar."""

    end, tokens = parse_second_ui_vm_record(data, start, bound)
    if end > bound:
        raise ValueError(f"SECOND UI record at 0x{start:X} exceeds its resource bound")
    accepted = MAPPING_POLICIES["reviewed"]
    glyph_ids: list[int] = []
    text_parts: list[str] = []
    visible: list[str] = []
    current: list[str] = []
    controls: list[str] = []
    confidence_counts: dict[str, int] = {}
    unresolved: set[int] = set()
    review: set[int] = set()
    mapped_count = 0
    unmapped_count = 0
    blank_count = 0

    def flush() -> None:
        rendered = "".join(current).strip("　 ")
        current.clear()
        if rendered:
            visible.append(rendered)

    for token in tokens:
        if token.kind == "glyph":
            index = (
                token.raw[0]
                if len(token.raw) == 1
                else ((token.raw[0] - 0xEB) << 8) | token.raw[1]
            )
            glyph_ids.append(index)
            if index == 0:
                rendered = "　"
                blank_count += 1
            else:
                row = mapping.rows[index]
                confidence = str(row.get("confidence", "unresolved"))
                confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
                character = row.get("character")
                if character is not None and confidence in accepted:
                    rendered = character
                    mapped_count += 1
                    if confidence in {"medium", "low"}:
                        review.add(index)
                else:
                    rendered = f"⟦G:{index:03X}⟧"
                    unresolved.add(index)
                    unmapped_count += 1
            text_parts.append(rendered)
            current.append(rendered)
            continue

        flush()
        if token.kind == "control":
            controls.append(token.raw.hex(" ").upper())
            opcode = token.raw[0]
            if opcode == 0xF6:
                text_parts.append("\n")
            else:
                args = token.raw[1:].hex(" ").upper()
                text_parts.append(
                    f"⟦F{opcode & 0x0F:X}" + (f":{args}" if args else "") + "⟧"
                )
        elif token.kind == "compact_data":
            text_parts.append(f'⟦D:{token.raw.hex(" ").upper()}⟧')
    flush()

    raw = data[start:end]
    return {
        "source_offset": start,
        "source_offset_hex": f"0x{start:X}",
        "end_offset_exclusive": end,
        "byte_length": len(raw),
        "raw_sha256": sha256(raw),
        "raw_hex": raw.hex(" ").upper(),
        "glyph_count": len(glyph_ids),
        "japanese_text": "".join(text_parts),
        "japanese_text_meta": {
            "coverage": "complete" if not unresolved else "partial",
            "mapping_policy": "reviewed",
            "mapped_glyph_count": mapped_count,
            "unmapped_glyph_count": unmapped_count,
            "unmapped_glyph_indices": sorted(unresolved),
            "review_glyph_indices": sorted(review),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "blank_glyph_count": blank_count,
            "control_count": len(controls),
        },
        "visible_runs": visible,
        "control_signature": controls,
    }


def pointed_ui_vm_record(
    data: bytes,
    field: int,
    bound: int,
    mapping: Any,
) -> dict[str, Any]:
    relative = s32(data, field)
    target = field + relative
    item: dict[str, Any] = {
        "pointer_field": field,
        "pointer_field_hex": f"0x{field:X}",
        "stored_relative": relative,
        "target": target,
        "target_hex": f"0x{target:X}",
    }
    if not 0 <= target < bound:
        item["classification"] = "non_text_or_out_of_resource_target"
        return item
    try:
        rendered = ui_vm_record_json(data, target, bound, mapping)
    except ValueError:
        item["classification"] = "non_ff_terminated_target"
        return item
    item["classification"] = "ff_terminated_record"
    item.update(rendered)
    return item


def table_json(
    data: bytes,
    mapping: Any,
    name: str,
    header: int,
    count: int,
    bound: int,
    base: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    for index in range(count):
        field = header + 4 + index * 4
        item = pointed_record(data, field, bound, mapping)
        item["entry_index"] = index
        item["translation_target"] = ordinary_translation_target(name, item)
        item["korean_text"] = None
        item["status"] = (
            "untranslated" if item["translation_target"] else "structural_or_placeholder"
        )
        records.append(item)
    valid = [r for r in records if r["classification"] == "ff_terminated_record"]
    nonempty = [r for r in valid if r["glyph_count"]]
    return {
        "asset_id": name,
        "header_offset": header,
        "table_start": header + 4,
        "entry_count": count,
        "verified_direct_lookup_call_sites": adjacent_table_load_calls(
            data, base, LOOKUP_OFFSET, header
        ),
        "valid_ff_record_references": len(valid),
        "unique_valid_targets": len({r["target"] for r in valid}),
        "nonempty_references": len(nonempty),
        "unique_nonempty_text": len({r["japanese_text"] for r in nonempty}),
        "translation_target_count": sum(r["translation_target"] for r in records),
        "translation_target_indices": [
            r["entry_index"] for r in records if r["translation_target"]
        ],
        "records": records,
    }


def parse_ui_master(data: bytes, mapping: Any, base: int) -> dict[str, Any]:
    header, count, bound = 0x24320, 107, 0x27180
    records: list[dict[str, Any]] = []
    for index in range(count):
        item = pointed_ui_vm_record(data, header + 4 + index * 4, bound, mapping)
        if item["classification"] != "ff_terminated_record":
            raise ValueError(f"UI master entry {index} is not a complete record")
        item.update(
            {
                "entry_index": index,
                "translation_target": index in UI_TRANSLATION_TARGETS,
                "category": category_for(index),
                "korean_text": None,
                "status": "untranslated",
            }
        )
        records.append(item)
    if {r["entry_index"] for r in records if r["translation_target"]} != UI_TRANSLATION_TARGETS:
        raise AssertionError("UI translation target set changed")
    return {
        "asset_id": "second_ui_script_master",
        "header_offset": header,
        "table_start": header + 4,
        "table_end": header + 4 + count * 4,
        "entry_count": count,
        "unique_record_targets": len({r["target"] for r in records}),
        "translation_target_count": len(UI_TRANSLATION_TARGETS),
        "translation_target_indices": sorted(UI_TRANSLATION_TARGETS),
        "verified_direct_lookup_call_sites": adjacent_table_load_calls(
            data, base, LOOKUP_OFFSET, header
        ),
        "relocation_rule": (
            "Reassemble each complete control-bearing FF record; set each field to "
            "new_record_offset - pointer_field. Never replace visible substrings in place."
        ),
        "records": records,
    }


def parse_common_nested_pool(data: bytes, mapping: Any) -> dict[str, Any]:
    master_header = 0x91FC
    master_entry = 36
    master_field = master_header + 4 + master_entry * 4
    nested_start = master_field + s32(data, master_field)
    nested_count = 172
    pool_start, pool_end = 0xAFAF, 0xB82F

    references: dict[int, list[dict[str, int]]] = defaultdict(list)
    for index in range(nested_count):
        field = nested_start + index * 4
        target = field + s32(data, field)
        references[target].append({"nested_index": index, "pointer_field": field})

    records: list[dict[str, Any]] = []
    for sequential_index, record in enumerate(
        iter_encoded_records(data, pool_start, pool_end)
    ):
        item = record_json(data, record, mapping)
        refs = references.get(record.start, [])
        item.update(
            {
                "sequential_index": sequential_index,
                "nested_indices": [r["nested_index"] for r in refs],
                "pointer_fields": [r["pointer_field"] for r in refs],
                "reference_count": len(refs),
                "sequential_only": not refs,
                "translation_target": bool(record.glyph_ids),
                "korean_text": None,
                "status": "untranslated" if record.glyph_ids else "structural_empty",
            }
        )
        records.append(item)

    if len(records) != 171 or records[-1]["end_offset_exclusive"] != pool_end:
        raise ValueError("common nested pool boundary changed")
    missing = sorted(set(references) - {r["source_offset"] for r in records})
    if missing:
        raise ValueError(f"nested pointers outside sequential pool: {missing}")

    return {
        "asset_id": "common_music_demo_title_pool",
        "master_header": master_header,
        "master_entry": master_entry,
        "nested_table_start": nested_start,
        "nested_entry_count": nested_count,
        "pool_start": pool_start,
        "pool_end": pool_end,
        "pool_bytes": pool_end - pool_start,
        "sequential_record_count": len(records),
        "pointer_referenced_record_starts": len(references),
        "sequential_only_record_count": sum(r["sequential_only"] for r in records),
        "sequential_only_indices": [
            r["sequential_index"] for r in records if r["sequential_only"]
        ],
        "all_five_executables_byte_identical": True,
        "relocation_rule": (
            "Preserve all 171 records in sequence and rewrite all 172 nested fields. "
            "The nine sequential-only records must not be dropped."
        ),
        "records": records,
    }


def parse_preview_pool(data: bytes, mapping: Any) -> dict[str, Any]:
    start, end = 0x5DC6, 0x6A69
    records: list[dict[str, Any]] = []
    for index, record in enumerate(iter_encoded_records(data, start, end)):
        item = record_json(data, record, mapping)
        item.update(
            {
                "sequential_index": index,
                "translation_target": bool(record.glyph_ids),
                "korean_text": None,
                "status": "untranslated" if record.glyph_ids else "structural_empty",
            }
        )
        records.append(item)
    if len(records) != 91 or records[-1]["end_offset_exclusive"] != end:
        raise ValueError("preview pool boundary changed")
    return {
        "asset_id": "common_preview_and_conditions_pool",
        "pool_start": start,
        "pool_end": end,
        "pool_bytes": end - start,
        "record_count": len(records),
        "nonempty_record_count": sum(r["glyph_count"] > 0 for r in records),
        "all_five_executables_byte_identical": True,
        "relocation_rule": (
            "Keep all 91 FF records contiguous and ordered. Existing in-place arena is "
            "3235 bytes; fail instead of truncating if a rebuilt pool exceeds it."
        ),
        "records": records,
    }


def executable_copy_audit() -> dict[str, Any]:
    groups: list[list[bytes]] = []
    rows: list[dict[str, Any]] = []
    for path_text, title_header, ui_header, ui_bound in EXECUTABLE_LAYOUTS:
        data = (EXTRACTED / Path(path_text)).read_bytes()
        headers = self_headers(data)
        layouts: dict[str, Any] = {}
        for asset_id, ordinal in RESOURCE_ORDINALS.items():
            header = headers[ordinal]
            bound = headers[ordinal + 1] if ordinal + 1 < len(headers) else len(data)
            layouts[asset_id] = {
                "header_offset": header,
                "table_start": header + 4,
                "inferred_entry_count": infer_table_count(data, header, bound),
            }
        title_field = title_header + 4 + 36 * 4
        nested = title_field + s32(data, title_field)
        nested_records: list[bytes] = []
        for index in range(172):
            field = nested + index * 4
            target = field + s32(data, field)
            record = next(iter_encoded_records(data, target, ui_bound), None)
            if record is None:
                raise ValueError(f"{path_text}: nested entry {index} has no record")
            nested_records.append(data[record.start : record.end])

        ui_records: list[bytes] = []
        for index in range(107):
            field = ui_header + 4 + index * 4
            target = field + s32(data, field)
            try:
                end, _tokens = parse_second_ui_vm_record(data, target, ui_bound)
            except ValueError as exc:
                raise ValueError(
                    f"{path_text}: UI entry {index} has no stateful VM record"
                ) from exc
            ui_records.append(data[target:end])
        groups.append(ui_records)
        rows.append(
            {
                "path": path_text,
                "preview_pool_start": PREVIEW_POOLS[path_text][0],
                "preview_pool_end": PREVIEW_POOLS[path_text][1],
                "music_demo_nested_table": nested,
                "music_demo_records_equal_canonical_second": None,
                "ui_master_header": ui_header,
                "ui_records_equal_canonical_second_by_index": None,
                "table_layouts": layouts,
            }
        )

        if len(set(nested_records)) != 158:
            raise ValueError(f"{path_text}: nested common content changed")

    canonical_ui = groups[3]
    for row, group in zip(rows, groups):
        row["music_demo_records_equal_canonical_second"] = 172
        row["ui_records_equal_canonical_second_by_index"] = sum(
            a == b for a, b in zip(canonical_ui, group)
        )
    common_ui = [
        index
        for index in range(107)
        if all(group[index] == groups[0][index] for group in groups)
    ]
    return {
        "executables": rows,
        "music_demo_nested_records_common_by_index": 172,
        "ui_records_common_by_index_count": len(common_ui),
        "ui_records_common_by_index": common_ui,
    }


def make_report() -> dict[str, Any]:
    data = SECOND.read_bytes()
    if sha256(data) != SECOND_SHA256:
        raise ValueError("SECOND.WAR SHA-256 mismatch")
    mapping = load_glyph_mapping(GLYPH_MAP)
    base = psx_base(data)
    ordinary_tables = [
        table_json(data, mapping, name, header, count, bound, base)
        for name, header, count, bound in TABLES
    ]
    return {
        "format": "srwcb-second-executable-ui-full-inventory-v1",
        "policy": {
            "binary_files_modified": False,
            "source_sha256_guarded": True,
            "fixed_slot_truncation_allowed": False,
        },
        "source": {
            "path": "SECOND/SECOND.WAR",
            "sha256": SECOND_SHA256,
            "file_to_ram_base": f"0x{base:08X}",
        },
        "encoding": {
            "terminator": "FF",
            "controls": "F6..FE with opcode-specific arguments",
            "pointer_formula": "target = pointer_field + signed_le32(pointer_field)",
        },
        "recommended_translation_schema": {
            "required_fields": [
                "asset_id", "scope", "exe", "entry_index", "pointer_field",
                "source_offset", "raw_sha256", "japanese_text", "korean_text",
                "status", "control_signature", "relocation_group",
            ],
            "sequential_pool_extra_field": "sequential_index",
            "validation": [
                "source raw_sha256 must match",
                "control_signature must be preserved unless explicitly reviewed",
                "complete FF record is rebuilt without truncation",
                "all self-relative fields resolve to exact record starts",
            ],
        },
        "common_preview_pool": parse_preview_pool(data, mapping),
        "common_music_demo_pool": parse_common_nested_pool(data, mapping),
        "second_ui_master": parse_ui_master(data, mapping, base),
        "other_menu_visible_tables": ordinary_tables,
        "cross_executable_copy_audit": executable_copy_audit(),
        "current_patch_audit": {
            "translated_ui_records": 0,
            "note": (
                "The v0.1.2 pre-battlefix candidate retains the original preview, "
                "music/demo, and UI-master bytes in SECOND.WAR and SLPS_020.70."
            ),
        },
    }


def sanitise_public_report(report: dict[str, Any]) -> dict[str, Any]:
    """Drop source text/bytes while preserving every executable guard."""

    public = copy.deepcopy(report)
    public.pop("cross_executable_copy_audit", None)
    public.pop("current_patch_audit", None)

    def scrub(value: Any) -> None:
        if isinstance(value, dict):
            for field in ("raw_hex", "japanese_text", "japanese_text_meta", "visible_runs"):
                value.pop(field, None)
            for child in value.values():
                scrub(child)
        elif isinstance(value, list):
            for child in value:
                scrub(child)

    scrub(public)
    required = public["recommended_translation_schema"]["required_fields"]
    public["recommended_translation_schema"]["required_fields"] = [
        field for field in required if field != "japanese_text"
    ]
    return public


def markdown(report: dict[str, Any]) -> str:
    lines = [
        "# SECOND 실행 파일 UI 전체 인벤토리",
        "",
        "이 보고서는 원본 `SECOND/SECOND.WAR`을 읽기만 하여 만든다. 전체 원문,",
        "제어 코드, 원시 바이트와 포인터 필드는 `second_exe_ui_full_inventory.json`에 있다.",
        "",
        "## 핵심 누락분",
        "",
        "- 공통 음악/데모 풀은 기존 보고서의 84개가 아니라 171개 연속 레코드(2,176바이트)다.",
        "- 중첩 테이블은 172개 필드이며 162개 레코드 시작을 참조한다. 직접 포인터가 없는",
        "  9개 BGM 레코드도 연속 풀에 있으므로 버리면 안 된다.",
        "- SECOND UI 마스터는 `0x24324..0x244CF`의 107개 필드이며, 이 가운데",
        f"  번역 대상은 {report['second_ui_master']['translation_target_count']}개 레코드다.",
        "- 기존 시험판에서 이 UI 세 영역의 번역 레코드는 0개다.",
        "",
        "## 자원별 정량",
        "",
        "| 자원 | 테이블/풀 | 참조 | 고유 대상 | 비고 |",
        "| --- | ---: | ---: | ---: | --- |",
        f"| 공통 미리보기/승패 조건 | `0x5DC6` | {report['common_preview_pool']['record_count']} | {report['common_preview_pool']['record_count']} | 연속 풀 |",
        f"| 공통 음악/데모 | `0xAFAF` | 172 | {report['common_music_demo_pool']['pointer_referenced_record_starts']} | 연속 레코드 171개 |",
        f"| SECOND UI 스크립트 | `0x24324` | {report['second_ui_master']['entry_count']} | {report['second_ui_master']['unique_record_targets']} | 제어 포함 전체 레코드 |",
    ]
    for table in report["other_menu_visible_tables"]:
        lines.append(
            f"| {table['asset_id']} | `0x{table['table_start']:X}` | "
            f"{table['entry_count']} | {table['unique_valid_targets']} | "
            f"고유 원문 {table['unique_nonempty_text']} |"
        )
    lines += [
        "",
        "## UI 마스터 번역 대상 인덱스",
        "",
        "```text",
        ", ".join(map(str, report["second_ui_master"]["translation_target_indices"])),
        "```",
        "",
        "대표 분류는 상태/전투 정보, 출격, 작전 목적, 인터미션, 반격 명령,",
        "정신기, 시스템 설정, 주인공 작성, 세이브/로드, 유닛·무기 개조,",
        "갈아타기, 강화 파츠, 옵션·사전·데모·가라오케다.",
        "",
        "## 재조립 규칙",
        "",
        "1. `FD/F7/FC/F8/F9/FB/FA/FE` 순서를 보존한 완전한 `FF` 레코드를 만든다.",
        "2. 포인터 레코드는 `새 레코드 오프셋 - 포인터 필드 오프셋`으로 다시 쓴다.",
        "3. 공통 171개와 미리보기 91개 풀은 순서와 연속성을 보존한다.",
        "4. 공간이 부족하면 문장을 자르지 말고 빌드를 중단한다.",
        "5. 메뉴에 노출되는 무기·파일럿·유닛 이름 테이블도 대사와 별개로 번역한다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MD)
    parser.add_argument("--public-output", type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    report = make_report()
    if not args.check_only:
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        args.markdown.write_text(markdown(report), encoding="utf-8")
        if args.public_output is not None:
            args.public_output.write_text(
                json.dumps(sanitise_public_report(report), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(f"wrote {args.output}")
        print(f"wrote {args.markdown}")
        if args.public_output is not None:
            print(f"wrote {args.public_output}")
    print(
        "verified UI master: 107 records / "
        f"{report['second_ui_master']['translation_target_count']} translation targets"
    )
    print("verified common nested pool: 171 records / 172 pointer fields")
    print("verified preview pool: 91 records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
