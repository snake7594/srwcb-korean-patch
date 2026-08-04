#!/usr/bin/env python3
"""Extract high-confidence SRW Complete Box text candidates.

The extractor is intentionally lossless: it preserves source offsets, raw
bytes, glyph indices, and control-code arguments.  With ``--glyph-map`` it also
adds a derived Japanese rendering while keeping the binary fields authoritative.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator


CONTROL_ARG_LENGTHS: dict[int, int] = {
    0xF6: 0,
    # F7 is a page/continue opcode with no operands.  The old two-byte
    # assumption swallowed the first one or two glyph bytes of the next page.
    0xF7: 0,
    0xF8: 1,
    0xF9: 1,
    0xFA: 0,
    0xFB: 2,
    0xFC: 2,
    0xFD: 2,
    0xFE: 1,
}

KNOWN_GLYPHS: dict[int, str] = {
    0x000: "　",
    0x03E: "「",
    0x03F: "」",
}

MAPPING_POLICIES: dict[str, frozenset[str]] = {
    "verified": frozenset({"exact", "verified"}),
    "conservative": frozenset({"exact", "verified", "high"}),
    "reviewed": frozenset(
        {"exact", "verified", "high", "medium", "low"}
    ),
}


@dataclass(frozen=True)
class SourceSpec:
    game: str
    kind: str
    relative_path: str
    format: str


SOURCE_SPECS: tuple[SourceSpec, ...] = (
    SourceSpec("main", "launcher_executable", "SLPS_020.70", "raw_executable"),
    SourceSpec("TR", "game_executable", "TR.WAR", "raw_executable"),
    SourceSpec("EX", "game_executable", "EX/EX.WAR", "raw_executable"),
    SourceSpec("EX", "scenario", "EX/E_SCE.BIN", "sce"),
    SourceSpec("EX", "death_quote", "EX/E_DEAD.BIN", "pointer_records"),
    SourceSpec("EX", "battle_message", "BMESS4.BIN", "bmess"),
    SourceSpec("SECOND", "game_executable", "SECOND/SECOND.WAR", "raw_executable"),
    SourceSpec("SECOND", "scenario", "SECOND/2_SCE.BIN", "sce"),
    SourceSpec("SECOND", "death_quote", "SECOND/2_DEAD.BIN", "pointer_records"),
    SourceSpec("SECOND", "battle_message", "BMESS2.BIN", "bmess"),
    SourceSpec("THIRD", "game_executable", "THIRD/THIRD.WAR", "raw_executable"),
    SourceSpec("THIRD", "scenario", "THIRD/3_SCE.BIN", "sce"),
    SourceSpec("THIRD", "death_quote", "THIRD/3_DEAD.BIN", "pointer_records"),
    SourceSpec("THIRD", "battle_message", "BMESS3.BIN", "bmess"),
)


@dataclass
class Region:
    start: int
    end: int
    archive_slot: int | None
    region_kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedRecord:
    start: int
    end: int
    tokens: list[dict[str, Any]]
    glyph_ids: list[int]
    preview: str


@dataclass(frozen=True)
class GlyphMapping:
    path: Path
    file_sha256: str
    font_sha256: str
    format: str
    rows: dict[int, dict[str, Any]]


def hex_offset(value: int) -> str:
    return f"0x{value:X}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def expected_message_bytes(index: int) -> str:
    if index < 0xEB:
        return f"{index:02X}"
    return f"{0xEB + (index >> 8):02X} {index & 0xFF:02X}"


def load_glyph_mapping(path: Path) -> GlyphMapping:
    raw = path.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    rows = document.get("rows")
    if not isinstance(rows, list) or len(rows) != 0xB00:
        raise ValueError("glyph map must contain exactly 2,816 rows")

    by_index: dict[int, dict[str, Any]] = {}
    for row in rows:
        index = row.get("glyph_index")
        if not isinstance(index, int) or not 0 <= index < 0xB00:
            raise ValueError(f"invalid glyph-map index {index!r}")
        if index in by_index:
            raise ValueError(f"duplicate glyph-map index 0x{index:03X}")
        actual_bytes = row.get("message_bytes", "").upper()
        if actual_bytes != expected_message_bytes(index):
            raise ValueError(
                f"glyph 0x{index:03X} has message bytes {actual_bytes!r}; "
                f"expected {expected_message_bytes(index)!r}"
            )
        character = row.get("character")
        if character is not None and not isinstance(character, str):
            raise ValueError(f"glyph 0x{index:03X} has a non-text character")
        by_index[index] = row

    if set(by_index) != set(range(0xB00)):
        raise ValueError("glyph map has missing indices")
    font_sha256 = document.get("font_sha256")
    if not isinstance(font_sha256, str) or len(font_sha256) != 64:
        raise ValueError("glyph map has no valid font SHA-256")
    return GlyphMapping(
        path=path.resolve(),
        file_sha256=sha256_bytes(raw),
        font_sha256=font_sha256,
        format=str(document.get("format", "unknown")),
        rows=by_index,
    )


def read_u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"u32 read outside file at {hex_offset(offset)}")
    return struct.unpack_from("<I", data, offset)[0]


def relative_pointer_table(data: bytes) -> tuple[int, list[int], list[int]]:
    """Decode pointers stored relative to the address of each pointer field."""

    if len(data) < 4:
        raise ValueError("file is too short to contain a pointer table")
    table_bytes = read_u32(data, 0)
    if table_bytes == 0 or table_bytes % 4:
        raise ValueError(f"invalid pointer table byte size {hex_offset(table_bytes)}")
    if table_bytes > len(data):
        raise ValueError(
            f"pointer table ({hex_offset(table_bytes)}) exceeds file size "
            f"({hex_offset(len(data))})"
        )

    pointer_count = table_bytes // 4
    stored: list[int] = []
    absolute: list[int] = []
    for index in range(pointer_count):
        field_offset = index * 4
        relative = read_u32(data, field_offset)
        target = field_offset + relative
        if target > len(data):
            raise ValueError(
                f"pointer {index} targets {hex_offset(target)}, beyond "
                f"{hex_offset(len(data))}"
            )
        stored.append(relative)
        absolute.append(target)

    if any(a > b for a, b in zip(absolute, absolute[1:])):
        raise ValueError("absolute pointer targets are not monotonic")
    return table_bytes, stored, absolute


def make_regions(
    spec: SourceSpec, data: bytes
) -> tuple[list[Region], dict[str, Any], list[str]]:
    """Return scan regions and structural metadata for one source."""

    if spec.format == "raw_executable":
        return (
            [Region(0, len(data), None, "raw_executable")],
            {
                "compression": "not_applicable",
                "boundary_confidence": "heuristic_ff_terminated_records",
            },
            [],
        )

    table_bytes, _stored, targets = relative_pointer_table(data)
    metadata: dict[str, Any] = {
        "pointer_table_bytes": table_bytes,
        "pointer_count": len(targets),
        "first_absolute_target": targets[0] if targets else None,
        "last_absolute_target": targets[-1] if targets else None,
        "pointer_formula": "absolute = stored_u32 + pointer_field_offset",
        "compression": "none_observed",
    }
    warnings: list[str] = []
    regions: list[Region] = []

    if spec.format == "sce":
        if len(targets) % 2:
            warnings.append("odd SCE pointer count; final unpaired target ignored")
        for scenario_index in range(len(targets) // 2):
            pointer_index = scenario_index * 2
            block_start = targets[pointer_index]
            pool_end = targets[pointer_index + 1]
            if block_start + 32 > len(data):
                warnings.append(
                    f"scenario {scenario_index} header is outside the file"
                )
                continue

            signature = {
                "u32_at_0x04": read_u32(data, block_start + 0x04),
                "u32_at_0x0C": read_u32(data, block_start + 0x0C),
                "u32_at_0x18": read_u32(data, block_start + 0x18),
                "u32_at_0x1C": read_u32(data, block_start + 0x1C),
            }
            signature_ok = signature == {
                "u32_at_0x04": 0x34,
                "u32_at_0x0C": 0x2C,
                "u32_at_0x18": 0x1DC,
                "u32_at_0x1C": 0x1C,
            }
            if not signature_ok:
                warnings.append(
                    f"scenario {scenario_index} has an unexpected header signature"
                )

            text_pool_relative = read_u32(data, block_start)
            pool_start = block_start + text_pool_relative
            if pool_start > pool_end or pool_end > len(data):
                warnings.append(
                    f"scenario {scenario_index} has invalid pool "
                    f"{hex_offset(pool_start)}..{hex_offset(pool_end)}"
                )
                continue

            regions.append(
                Region(
                    pool_start,
                    pool_end,
                    pointer_index,
                    "scenario_text_pool",
                    {
                        "scenario_index": scenario_index,
                        "scenario_block_start": block_start,
                        "text_pool_relative": text_pool_relative,
                        "header_signature_ok": signature_ok,
                    },
                )
            )

        metadata.update(
            {
                "scenario_count": len(regions),
                "layout": (
                    "even pointer = scenario block start; odd pointer = text-pool end; "
                    "text-pool start = block start + first u32"
                ),
            }
        )
        return regions, metadata, warnings

    if spec.format == "bmess":
        empty_blocks = 0
        non_cpe_blocks = 0
        for slot, (start, end) in enumerate(zip(targets, targets[1:])):
            if start == end:
                empty_blocks += 1
                continue
            has_cpe_magic = data[start : start + 4] == b"CPE\x01"
            if not has_cpe_magic:
                non_cpe_blocks += 1
            regions.append(
                Region(
                    start,
                    end,
                    slot,
                    "battle_message_block",
                    {"cpe_magic": has_cpe_magic},
                )
            )
        metadata.update(
            {
                "interval_count": max(0, len(targets) - 1),
                "nonempty_block_count": len(regions),
                "empty_block_count": empty_blocks,
                "non_cpe_block_count": non_cpe_blocks,
                "block_magic": "43 50 45 01 (CPE\\x01)",
            }
        )
        return regions, metadata, warnings

    if spec.format == "pointer_records":
        empty_blocks = 0
        for slot, start in enumerate(targets):
            end = targets[slot + 1] if slot + 1 < len(targets) else len(data)
            if start == end:
                empty_blocks += 1
                continue
            regions.append(
                Region(start, end, slot, "pointer_record")
            )
        metadata.update(
            {
                "interval_count": len(targets),
                "nonempty_block_count": len(regions),
                "empty_block_count": empty_blocks,
                "final_interval_ends_at_eof": True,
            }
        )
        return regions, metadata, warnings

    raise ValueError(f"unsupported source format {spec.format!r}")


def iter_encoded_records(data: bytes, start: int, end: int) -> Iterator[ParsedRecord]:
    """Split a region into FF-terminated records using the renderer bytecode."""

    cursor = start
    record_start = start
    tokens: list[dict[str, Any]] = []
    glyph_ids: list[int] = []
    preview_parts: list[str] = []

    while cursor < end:
        token_offset = cursor
        opcode = data[cursor]

        if opcode < 0xEB:
            glyph_id = opcode
            tokens.append(
                {
                    "type": "glyph",
                    "offset": token_offset,
                    "index": glyph_id,
                    "raw_hex": f"{opcode:02X}",
                }
            )
            glyph_ids.append(glyph_id)
            preview_parts.append(KNOWN_GLYPHS.get(glyph_id, f"<G:{glyph_id:03X}>"))
            cursor += 1
            continue

        if opcode < 0xF6:
            if cursor + 1 >= end:
                break
            second = data[cursor + 1]
            glyph_id = ((opcode - 0xEB) << 8) | second
            tokens.append(
                {
                    "type": "glyph",
                    "offset": token_offset,
                    "index": glyph_id,
                    "raw_hex": f"{opcode:02X} {second:02X}",
                }
            )
            glyph_ids.append(glyph_id)
            preview_parts.append(KNOWN_GLYPHS.get(glyph_id, f"<G:{glyph_id:03X}>"))
            cursor += 2
            continue

        if opcode == 0xFF:
            tokens.append(
                {
                    "type": "terminator",
                    "offset": token_offset,
                    "opcode": 0xFF,
                    "raw_hex": "FF",
                }
            )
            cursor += 1
            yield ParsedRecord(
                record_start,
                cursor,
                tokens,
                glyph_ids,
                "".join(preview_parts),
            )
            record_start = cursor
            tokens = []
            glyph_ids = []
            preview_parts = []
            continue

        arg_length = CONTROL_ARG_LENGTHS[opcode]
        control_end = cursor + 1 + arg_length
        if control_end > end:
            break
        args = data[cursor + 1 : control_end]
        raw = data[cursor:control_end]
        tokens.append(
            {
                "type": "control",
                "offset": token_offset,
                "opcode": opcode,
                "args_hex": args.hex(" ").upper(),
                "raw_hex": raw.hex(" ").upper(),
            }
        )
        if opcode == 0xF6:
            preview_parts.append("\n")
        else:
            suffix = f":{args.hex().upper()}" if args else ""
            preview_parts.append(f"<F{opcode & 0x0F:X}{suffix}>")
        cursor = control_end


def bracketed(glyph_ids: list[int]) -> bool:
    try:
        opening = glyph_ids.index(0x03E)
        glyph_ids.index(0x03F, opening + 1)
    except ValueError:
        return False
    return True


def control_tag(token: dict[str, Any]) -> str:
    opcode = token["opcode"]
    args = token.get("args_hex", "")
    suffix = f":{args}" if args else ""
    return f"⟦F{opcode & 0x0F:X}{suffix}⟧"


def render_japanese(
    record: ParsedRecord,
    mapping: GlyphMapping,
    mapping_policy: str,
) -> dict[str, Any]:
    accepted = MAPPING_POLICIES[mapping_policy]
    text_parts: list[str] = []
    confidence_counts: dict[str, int] = {}
    unresolved: set[int] = set()
    review: set[int] = set()
    mapped_count = 0
    blank_glyph_count = 0
    control_count = 0

    for token in record.tokens:
        token_type = token["type"]
        if token_type == "terminator":
            continue
        if token_type == "control":
            control_count += 1
            if token["opcode"] == 0xF6:
                text_parts.append("\n")
            else:
                tag = control_tag(token)
                text_parts.append(tag)
            continue

        index = token["index"]
        if index == 0:
            # Cell zero is a byte-for-byte blank glyph used for spacing.  F6,
            # not this glyph, is the renderer's explicit line-advance opcode.
            text_parts.append("　")
            blank_glyph_count += 1
            continue

        row = mapping.rows[index]
        confidence = str(row.get("confidence", "unresolved"))
        confidence_counts[confidence] = confidence_counts.get(confidence, 0) + 1
        character = row.get("character")
        if character is not None and confidence in accepted:
            text_parts.append(character)
            mapped_count += 1
            if confidence in {"medium", "low"}:
                review.add(index)
        else:
            placeholder = f"⟦G:{index:03X}⟧"
            text_parts.append(placeholder)
            unresolved.add(index)

    return {
        "japanese_text": "".join(text_parts),
        "japanese_text_meta": {
            "coverage": "complete" if not unresolved else "partial",
            "mapping_policy": mapping_policy,
            "mapped_glyph_count": mapped_count,
            "unmapped_glyph_count": sum(
                1
                for index in record.glyph_ids
                if index != 0
                and (
                    mapping.rows[index].get("character") is None
                    or mapping.rows[index].get("confidence") not in accepted
                )
            ),
            "unmapped_glyph_indices": sorted(unresolved),
            "review_glyph_indices": sorted(review),
            "confidence_counts": dict(sorted(confidence_counts.items())),
            "blank_glyph_count": blank_glyph_count,
            "control_count": control_count,
        },
    }


def candidate_dict(
    spec: SourceSpec,
    data: bytes,
    record: ParsedRecord,
    region: Region,
    ordinal: int,
    confidence: str,
    glyph_mapping: GlyphMapping | None,
    mapping_policy: str,
) -> dict[str, Any]:
    raw = data[record.start : record.end]
    item: dict[str, Any] = {
        "id": f"{spec.game.lower()}:{spec.kind}:{ordinal:06d}",
        "occurrence_id": f"{spec.relative_path}@{record.start:08X}",
        "game": spec.game,
        "source_kind": spec.kind,
        "source_path": spec.relative_path,
        "source_format": spec.format,
        "confidence": confidence,
        "offset": record.start,
        "offset_hex": hex_offset(record.start),
        "end_offset_exclusive": record.end,
        "end_offset_exclusive_hex": hex_offset(record.end),
        "byte_length": record.end - record.start,
        "archive_slot": region.archive_slot,
        "region_kind": region.region_kind,
        "region_start": region.start,
        "region_end": region.end,
        "glyph_count": len(record.glyph_ids),
        "glyph_indices": record.glyph_ids,
        "preview": record.preview,
        "raw_hex": raw.hex(" ").upper(),
        "raw_sha256": sha256_bytes(raw),
        "tokens": record.tokens,
    }
    item.update(region.metadata)
    if glyph_mapping is not None:
        item.update(render_japanese(record, glyph_mapping, mapping_policy))
    return item


def extract_source(
    extracted_root: Path,
    spec: SourceSpec,
    *,
    min_glyphs: int,
    include_unbracketed: bool,
    max_raw_record_bytes: int,
    sample_per_source: int,
    glyph_mapping: GlyphMapping | None,
    mapping_policy: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = extracted_root / Path(spec.relative_path)
    data = path.read_bytes()
    regions, structural, warnings = make_regions(spec, data)

    emitted: list[dict[str, Any]] = []
    candidate_count = 0
    candidate_bytes = 0
    first_offset: int | None = None
    last_offset: int | None = None

    for region in regions:
        for record in iter_encoded_records(data, region.start, region.end):
            record_length = record.end - record.start
            if len(record.glyph_ids) < min_glyphs:
                continue
            if spec.format == "raw_executable" and record_length > max_raw_record_bytes:
                continue

            has_brackets = bracketed(record.glyph_ids)
            if not has_brackets and not include_unbracketed:
                continue
            confidence = (
                "high_bracketed"
                if has_brackets and spec.format != "raw_executable"
                else "heuristic_bracketed"
                if has_brackets
                else "medium_unbracketed"
                if spec.format != "raw_executable"
                else "low_unbracketed"
            )

            ordinal = candidate_count
            candidate_count += 1
            candidate_bytes += record_length
            if first_offset is None:
                first_offset = record.start
            last_offset = record.end - 1
            if sample_per_source == 0 or len(emitted) < sample_per_source:
                emitted.append(
                    candidate_dict(
                        spec,
                        data,
                        record,
                        region,
                        ordinal,
                        confidence,
                        glyph_mapping,
                        mapping_policy,
                    )
                )

    summary: dict[str, Any] = {
        "game": spec.game,
        "source_kind": spec.kind,
        "path": spec.relative_path,
        "format": spec.format,
        "size": len(data),
        "sha256": sha256_bytes(data),
        "region_count": len(regions),
        "candidate_count": candidate_count,
        "candidate_bytes": candidate_bytes,
        "first_candidate_offset": first_offset,
        "last_candidate_byte_offset": last_offset,
        "emitted_candidate_count": len(emitted),
        "warnings": warnings,
    }
    summary.update(structural)
    return summary, emitted


def build_document(
    extracted_root: Path,
    specs: Iterable[SourceSpec],
    *,
    min_glyphs: int,
    include_unbracketed: bool,
    max_raw_record_bytes: int,
    sample_per_source: int,
    glyph_mapping: GlyphMapping | None = None,
    mapping_policy: str = "reviewed",
) -> dict[str, Any]:
    summaries: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []

    for spec in specs:
        summary, source_candidates = extract_source(
            extracted_root,
            spec,
            min_glyphs=min_glyphs,
            include_unbracketed=include_unbracketed,
            max_raw_record_bytes=max_raw_record_bytes,
            sample_per_source=sample_per_source,
            glyph_mapping=glyph_mapping,
            mapping_policy=mapping_policy,
        )
        summaries.append(summary)
        candidates.extend(source_candidates)

    document: dict[str, Any] = {
        "schema": (
            "srwcb-dialogue-candidates-v2"
            if glyph_mapping is not None
            else "srwcb-dialogue-candidates-v1"
        ),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "extracted_root": str(extracted_root.resolve()),
        "encoding": {
            "single_byte_glyph": "byte < 0xEB: glyph_index = byte",
            "double_byte_glyph": (
                "0xEB <= byte < 0xF6: glyph_index = ((byte - 0xEB) << 8) "
                "| next_byte"
            ),
            "control_range": "0xF6..0xFE",
            "terminator": "0xFF",
            "control_argument_lengths": {
                f"0x{opcode:02X}": length
                for opcode, length in CONTROL_ARG_LENGTHS.items()
            },
            "known_glyphs": {
                f"0x{index:03X}": char for index, char in KNOWN_GLYPHS.items()
            },
        },
        "selection": {
            "minimum_glyph_count": min_glyphs,
            "require_open_then_close_quote": not include_unbracketed,
            "opening_quote_glyph": "0x03E",
            "closing_quote_glyph": "0x03F",
            "raw_executable_max_record_bytes": max_raw_record_bytes,
            "sample_per_source": sample_per_source,
            "note": (
                "Bracketed records are high-confidence dialogue candidates, not an "
                "exhaustive inventory of every UI or narration string."
            ),
        },
        "sources": summaries,
        "candidates": candidates,
    }
    if glyph_mapping is not None:
        document["glyph_mapping"] = {
            "path": str(glyph_mapping.path),
            "sha256": glyph_mapping.file_sha256,
            "font_sha256": glyph_mapping.font_sha256,
            "format": glyph_mapping.format,
            "policy": mapping_policy,
            "accepted_confidences": sorted(MAPPING_POLICIES[mapping_policy]),
            "unicode_normalization": "none",
            "unmapped_template": "⟦G:{index:03X}⟧",
            "rendering": {
                "glyph_0x000_in_japanese_text": "U+3000 IDEOGRAPHIC SPACE",
                "control_0xF6": "line feed",
                "controls_0xF7_to_0xFE": "⟦opcode:arguments⟧ tag",
                "terminator_0xFF": "omitted from rendered text",
            },
        }
    return document


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "extracted_root",
        type=Path,
        help="root containing SLPS_020.70, BMESS*.BIN, and game subdirectories",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="write JSON here; omit to write UTF-8 JSON to stdout",
    )
    parser.add_argument(
        "--glyph-map",
        type=Path,
        help="2,816-row font mapping JSON; adds derived Japanese text fields",
    )
    parser.add_argument(
        "--mapping-policy",
        choices=tuple(MAPPING_POLICIES),
        default="reviewed",
        help=(
            "accepted mapping confidences: verified, conservative, or reviewed "
            "(default: reviewed)"
        ),
    )
    parser.add_argument(
        "--archives-only",
        action="store_true",
        help="exclude heuristic executable scans; retain SCE/DEAD/BMESS only",
    )
    parser.add_argument(
        "--sample-per-source",
        type=int,
        default=0,
        metavar="N",
        help="emit only the first N candidates per source while retaining full counts",
    )
    parser.add_argument(
        "--min-glyphs",
        type=int,
        default=4,
        metavar="N",
        help="minimum decoded glyph count (default: 4)",
    )
    parser.add_argument(
        "--include-unbracketed",
        action="store_true",
        help="also emit lower-confidence records without 「...」 glyphs",
    )
    parser.add_argument(
        "--max-raw-record-bytes",
        type=int,
        default=512,
        metavar="N",
        help="maximum record size accepted from executables (default: 512)",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation (default: 2)",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="write compact JSON without indentation",
    )
    args = parser.parse_args(argv)
    if args.sample_per_source < 0:
        parser.error("--sample-per-source must be non-negative")
    if args.min_glyphs < 1:
        parser.error("--min-glyphs must be at least 1")
    if args.max_raw_record_bytes < 1:
        parser.error("--max-raw-record-bytes must be at least 1")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        glyph_mapping = (
            load_glyph_mapping(args.glyph_map) if args.glyph_map is not None else None
        )
        specs = (
            tuple(spec for spec in SOURCE_SPECS if spec.format != "raw_executable")
            if args.archives_only
            else SOURCE_SPECS
        )
        document = build_document(
            args.extracted_root,
            specs,
            min_glyphs=args.min_glyphs,
            include_unbracketed=args.include_unbracketed,
            max_raw_record_bytes=args.max_raw_record_bytes,
            sample_per_source=args.sample_per_source,
            glyph_mapping=glyph_mapping,
            mapping_policy=args.mapping_policy,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(
        document,
        ensure_ascii=False,
        indent=None if args.compact else args.indent,
        separators=(",", ":") if args.compact else None,
    ) + "\n"
    if args.output is None:
        sys.stdout.buffer.write(rendered.encode("utf-8"))
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
