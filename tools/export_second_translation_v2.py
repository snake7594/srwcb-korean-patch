#!/usr/bin/env python3
"""Build a lossless translation ledger for SRW Complete Box: SECOND.

This exporter deliberately contains no machine translation and no binary patch
logic. It converts the reviewed Japanese candidate dump into a smaller,
translation-facing document while retaining immutable source identity, control
codes and original layout information.

The old fixed-slot build is not an input. In particular, this script never
copies text from "second_full_translation_cache.rejected.json".
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

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from analyze_second_message_archives import parse_bmess, parse_dead


ROOT = _P.WORK
INPUT = _P.LEDGER / "dialogue_japanese.full.json"
MAPPING = _P.FONT_MAPPING
OUT_DIR = _P.LEDGER
LEDGER = OUT_DIR / "second_translation_ledger.json"
GLOSSARY = OUT_DIR / "glossary_candidates.json"
EXTRACTED = _P.EXTRACTED

TARGETS = {
    "SECOND/2_SCE.BIN": "scenario",
    "BMESS2.BIN": "battle_message",
    "SECOND/2_DEAD.BIN": "death_quote",
}

CONTROL_TAG = re.compile(r"⟦F([6-9A-F])(?::([^\]]*?))?⟧")
KATAKANA_TERM = re.compile(r"[ァ-ヺー・=]{2,}")
KANJI_TERM = re.compile(r"[\u3400-\u9fff々〆ヵヶ]{2,}")
CONTROL_ARG_LENGTHS = {
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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_id(prefix: str, value: str, length: int = 16) -> str:
    return f"{prefix}-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:length]}"


def normalise_long_mark(text: str) -> str:
    """Render glyph 0x011 as Japanese ー, preserving masked speaker '---'."""

    return re.sub(
        r"-+",
        lambda match: match.group(0) if len(match.group(0)) >= 2 else "ー",
        text,
    )


def load_glyph_characters() -> dict[int, str]:
    document = json.loads(MAPPING.read_text(encoding="utf-8"))
    result: dict[int, str] = {0: "　"}
    for row in document["rows"]:
        character = row.get("character")
        if character:
            result[int(row["glyph_index"])] = character
    return result


def decode_raw_record(candidate: dict[str, Any], glyphs: dict[int, str]) -> str:
    """Decode one record with the corrected zero-argument F7 command."""

    raw = bytes.fromhex(candidate["raw_hex"])
    out: list[str] = []
    cursor = 0
    while cursor < len(raw):
        opcode = raw[cursor]
        if opcode < 0xEB:
            out.append(glyphs.get(opcode, f"⟦G:{opcode:03X}⟧"))
            cursor += 1
            continue
        if opcode < 0xF6:
            if cursor + 1 >= len(raw):
                raise ValueError(f"truncated glyph in {candidate['occurrence_id']}")
            index = ((opcode - 0xEB) << 8) | raw[cursor + 1]
            out.append(glyphs.get(index, f"⟦G:{index:03X}⟧"))
            cursor += 2
            continue
        if opcode == 0xFF:
            if cursor != len(raw) - 1:
                raise ValueError(f"bytes follow terminator in {candidate['occurrence_id']}")
            return "".join(out)
        arg_length = CONTROL_ARG_LENGTHS[opcode]
        end = cursor + 1 + arg_length
        if end > len(raw):
            raise ValueError(f"truncated control in {candidate['occurrence_id']}")
        args_hex = raw[cursor + 1 : end].hex(" ").upper()
        if opcode == 0xF6:
            out.append("\n")
        else:
            suffix = f":{args_hex}" if args_hex else ""
            out.append(f"⟦F{opcode & 0x0F:X}{suffix}⟧")
        cursor = end
    raise ValueError(f"record has no terminator: {candidate['occurrence_id']}")


def parse_control_tag(match: re.Match[str]) -> dict[str, Any]:
    opcode = int(match.group(1), 16) | 0xF0
    args_hex = (match.group(2) or "").strip().upper()
    raw_hex = f"{opcode:02X}" + (f" {args_hex}" if args_hex else "")
    return {
        "kind": "page_break" if opcode == 0xF7 else "control",
        "opcode": f"0x{opcode:02X}",
        "args_hex": args_hex,
        "raw_hex": raw_hex,
    }


def split_tagged_text(text: str, *, keep_line_breaks: bool) -> list[dict[str, Any]]:
    """Split rendered Japanese into text/control/(optional) line-break parts."""

    parts: list[dict[str, Any]] = []
    cursor = 0
    pattern = re.compile(r"\n|⟦F([6-9A-F])(?::([^\]]*?))?⟧")
    for match in pattern.finditer(text):
        if match.start() > cursor:
            parts.append(
                {
                    "kind": "text",
                    "ja": normalise_long_mark(text[cursor : match.start()]),
                }
            )
        marker = match.group(0)
        if marker == "\n":
            if keep_line_breaks:
                parts.append({"kind": "line_break", "raw_hex": "F6"})
        else:
            parts.append(parse_control_tag(match))
        cursor = match.end()
    if cursor < len(text):
        parts.append({"kind": "text", "ja": normalise_long_mark(text[cursor:])})
    return parts


def source_parts(full_text: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return lossless full-record layout parts and translation parts.

    F6 line advances are layout, so they remain in source_layout_parts but are
    removed from translation_parts. Original F7 page boundaries and F8..FE
    commands remain anchors. Adjacent text split only by old line wrapping is
    joined back together.
    """

    layout_parts = split_tagged_text(full_text, keep_line_breaks=True)
    semantic: list[dict[str, Any]] = []
    text_buffer: list[str] = []
    text_index = 0
    control_index = 0
    break_index = 0

    def flush() -> None:
        nonlocal text_index
        if not text_buffer:
            return
        value = "".join(text_buffer)
        semantic.append({"kind": "text", "part_id": f"p{text_index:02d}", "ja": value})
        text_index += 1
        text_buffer.clear()

    for part in layout_parts:
        if part["kind"] == "text":
            text_buffer.append(part["ja"])
        elif part["kind"] == "line_break":
            continue
        else:
            flush()
            anchor = dict(part)
            if part["kind"] == "page_break":
                anchor["part_id"] = f"b{break_index:02d}"
                break_index += 1
            else:
                anchor["part_id"] = f"c{control_index:02d}"
                control_index += 1
            semantic.append(anchor)
    flush()
    speaker_pattern = re.compile(r"(?:^|(?<=」))([　 ]*)([^「」\n]{1,20})「")
    for part in semantic:
        if part["kind"] != "text":
            continue
        mentions: list[dict[str, Any]] = []
        for match in speaker_pattern.finditer(part["ja"]):
            mentions.append(
                {
                    "label_id": "",
                    "ja": normalise_long_mark(match.group(2)).strip(),
                    "start": match.start(2),
                    "end": match.end(2),
                }
            )
        part["speaker_mentions"] = mentions
    mention_index = 0
    for part in semantic:
        for mention in part.get("speaker_mentions", []):
            mention["label_id"] = f"s{mention_index:02d}"
            mention_index += 1
    return layout_parts, semantic


def strip_controls(text: str) -> str:
    return CONTROL_TAG.sub("", text)


def valid_candidate(candidate: dict[str, Any]) -> tuple[bool, str | None]:
    text = candidate["_decoded_japanese"]
    source_path = candidate.get("source_path")
    meta = candidate.get("japanese_text_meta", {})
    if meta.get("coverage") != "complete":
        return False, "incomplete_glyph_mapping"
    if source_path == "BMESS2.BIN":
        if not (text.startswith("「") and text.endswith("」")):
            return False, "battle_binary_false_positive"
    elif "「" not in text or not text.endswith("」"):
        return False, "missing_dialogue_envelope"
    return True, None


def rejection(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "id": candidate["occurrence_id"],
        "source_path": candidate["source_path"],
        "offset_hex": candidate["offset_hex"],
        "reason": reason,
        "japanese_text": candidate.get("_decoded_japanese", candidate.get("japanese_text")),
    }


def runtime_candidate(
    existing: dict[str, Any] | None,
    *,
    source_path: str,
    offset: int,
    raw: bytes,
    archive_slot: int,
    region_start: int,
    region_end: int,
    runtime_reference: dict[str, Any],
    glyphs: dict[int, str],
) -> tuple[dict[str, Any], bool]:
    """Return a candidate backed by a proven runtime reference.

    The reviewed heuristic dump is reused when it has the exact same start and
    bytes.  Records that the old scanner missed (nested BMESS leaves and two
    2_DEAD slots) are reconstructed directly from the immutable source bytes.
    """

    occurrence_id = f"{source_path}@{offset:08X}"
    raw_hex = raw.hex(" ").upper()
    if existing is not None:
        if bytes.fromhex(existing["raw_hex"]) != raw:
            raise ValueError(f"runtime/source raw mismatch at {occurrence_id}")
        candidate = dict(existing)
        synthesized = False
    else:
        candidate = {
            "occurrence_id": occurrence_id,
            "source_path": source_path,
            "offset": offset,
            "offset_hex": f"0x{offset:X}",
            "end_offset_exclusive": offset + len(raw),
            "byte_length": len(raw),
            "raw_hex": raw_hex,
            "raw_sha256": sha256_bytes(raw),
            "scenario_index": None,
            "archive_slot": archive_slot,
            "region_start": region_start,
            "region_end": region_end,
            "japanese_text_meta": {
                "coverage": "complete",
                "mapping_policy": "reviewed_runtime_reconstruction",
            },
        }
        synthesized = True

    candidate["_runtime_reference"] = runtime_reference
    candidate["_decoded_japanese"] = decode_raw_record(candidate, glyphs)
    if synthesized:
        candidate["japanese_text"] = candidate["_decoded_japanese"]
    return candidate, synthesized


def select_authoritative_candidates(
    candidates: list[dict[str, Any]],
    glyphs: dict[int, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Select SCE records as before and pointer-proven BMESS/DEAD records."""

    by_source: dict[str, dict[int, dict[str, Any]]] = {
        source_path: {} for source_path in TARGETS
    }
    ordered_by_source: dict[str, list[dict[str, Any]]] = {
        source_path: [] for source_path in TARGETS
    }
    for candidate in candidates:
        source_path = candidate["source_path"]
        offset = candidate["offset"]
        if offset in by_source[source_path]:
            raise ValueError(f"duplicate heuristic candidate at {source_path}@{offset:#x}")
        candidate["_decoded_japanese"] = decode_raw_record(candidate, glyphs)
        by_source[source_path][offset] = candidate
        ordered_by_source[source_path].append(candidate)

    accepted_sce: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for candidate in ordered_by_source["SECOND/2_SCE.BIN"]:
        valid, reason = valid_candidate(candidate)
        if valid:
            accepted_sce.append(candidate)
        else:
            rejected.append(rejection(candidate, reason or "scenario_candidate_rejected"))

    bmess_data = (EXTRACTED / "BMESS2.BIN").read_bytes()
    bmess = parse_bmess(bmess_data)
    accepted_bmess: list[dict[str, Any]] = []
    bmess_live_offsets: set[int] = set()
    bmess_unreferenced_offsets: set[int] = set()
    bmess_synthesized: list[str] = []
    for block in bmess.blocks:
        for target in block.unreferenced_quoted_records:
            bmess_unreferenced_offsets.add(block.file_start + 15 + target)
        for target, record in sorted(block.text_records.items()):
            offset = block.file_start + 15 + target
            raw = block.payload[record.start : record.end]
            reference_fields = list(block.text_references[target])
            runtime_reference = {
                "format": "bmess_block_leaf",
                "block_index": block.index,
                "payload_text_offset": target,
                "payload_text_offset_hex": f"0x{target:X}",
                "leaf_reference_fields": reference_fields,
                "leaf_reference_count": len(reference_fields),
            }
            candidate, synthesized = runtime_candidate(
                by_source["BMESS2.BIN"].get(offset),
                source_path="BMESS2.BIN",
                offset=offset,
                raw=raw,
                archive_slot=block.index,
                region_start=block.file_start,
                region_end=block.file_end,
                runtime_reference=runtime_reference,
                glyphs=glyphs,
            )
            accepted_bmess.append(candidate)
            bmess_live_offsets.add(offset)
            if synthesized:
                bmess_synthesized.append(candidate["occurrence_id"])

    for candidate in ordered_by_source["BMESS2.BIN"]:
        offset = candidate["offset"]
        if offset in bmess_live_offsets:
            continue
        reason = (
            "bmess_unreferenced_quoted_record"
            if offset in bmess_unreferenced_offsets
            else "bmess_not_runtime_text_record"
        )
        rejected.append(rejection(candidate, reason))

    dead_data = (EXTRACTED / "SECOND" / "2_DEAD.BIN").read_bytes()
    dead = parse_dead(dead_data)
    accepted_dead: list[dict[str, Any]] = []
    dead_synthesized: list[str] = []
    live_slots = {
        start: (slot_index, end)
        for slot_index, (start, end) in enumerate(dead.slots)
        if start != end
    }
    for start, record in sorted(dead.records.items()):
        slot_index, end = live_slots[start]
        raw = dead.source[record.start : record.end]
        runtime_reference = {
            "format": "dead_start_end_slot",
            "slot_index": slot_index,
            "start_pointer_index": slot_index * 2,
            "end_pointer_index": slot_index * 2 + 1,
        }
        candidate, synthesized = runtime_candidate(
            by_source["SECOND/2_DEAD.BIN"].get(start),
            source_path="SECOND/2_DEAD.BIN",
            offset=start,
            raw=raw,
            archive_slot=slot_index * 2,
            region_start=start,
            region_end=end,
            runtime_reference=runtime_reference,
            glyphs=glyphs,
        )
        accepted_dead.append(candidate)
        if synthesized:
            dead_synthesized.append(candidate["occurrence_id"])

    dead_live_offsets = set(dead.records)
    for candidate in ordered_by_source["SECOND/2_DEAD.BIN"]:
        if candidate["offset"] not in dead_live_offsets:
            rejected.append(rejection(candidate, "dead_not_runtime_slot"))

    runtime_summary = {
        "parser": "korean_patch/tools/analyze_second_message_archives.py",
        "bmess": {
            "source_sha256": sha256_bytes(bmess_data),
            "block_count": len(bmess.blocks),
            "leaf_reference_count": sum(
                len(fields)
                for block in bmess.blocks
                for fields in block.text_references.values()
            ),
            "live_text_record_count": len(bmess_live_offsets),
            "unreferenced_quoted_record_count": len(bmess_unreferenced_offsets),
            "synthesized_occurrence_ids": bmess_synthesized,
        },
        "dead": {
            "source_sha256": sha256_bytes(dead_data),
            "slot_count": len(dead.slots),
            "live_text_record_count": len(dead.records),
            "empty_slot_count": sum(start == end for start, end in dead.slots),
            "synthesized_occurrence_ids": dead_synthesized,
        },
    }

    # Preserve the established source order: SCE, 2_DEAD, then BMESS.
    accepted = [*accepted_sce, *accepted_dead, *accepted_bmess]
    return accepted, rejected, runtime_summary


def make_occurrence(candidate: dict[str, Any]) -> dict[str, Any]:
    extracted = candidate["_decoded_japanese"]
    full_normalised = normalise_long_mark(extracted)
    layout_parts, translation_parts = source_parts(full_normalised)
    speaker_mentions = [
        {"part_id": part["part_id"], **mention}
        for part in translation_parts
        if part["kind"] == "text"
        for mention in part["speaker_mentions"]
    ]
    duplicate_key = stable_id("tm", full_normalised)
    source_path = candidate["source_path"]
    occurrence_id = candidate["occurrence_id"]
    source = {
        "path": source_path,
        "offset": candidate["offset"],
        "offset_hex": candidate["offset_hex"],
        "end_offset_exclusive": candidate["end_offset_exclusive"],
        "byte_length": candidate["byte_length"],
        "raw_hex": candidate["raw_hex"],
        "raw_sha256": candidate["raw_sha256"],
        "scenario_index": candidate.get("scenario_index"),
        "archive_slot": candidate.get("archive_slot"),
        "region_start": candidate.get("region_start"),
        "region_end": candidate.get("region_end"),
    }
    if candidate.get("_runtime_reference") is not None:
        source["runtime_reference"] = candidate["_runtime_reference"]

    return {
        "id": occurrence_id,
        "kind": TARGETS[source_path],
        "source": source,
        "japanese": {
            "extracted_full": extracted,
            "legacy_extracted_full": (
                candidate["japanese_text"]
                if candidate.get("japanese_text") != extracted
                else None
            ),
            "normalised_full": full_normalised,
            "plain_full": strip_controls(full_normalised).replace("\n", ""),
            "source_layout_parts": layout_parts,
            "translation_parts": translation_parts,
            "speaker_mentions": speaker_mentions,
        },
        "translation": {
            "status": "untranslated",
            "ko_parts": {
                part["part_id"]: None
                for part in translation_parts
                if part["kind"] == "text"
            },
            "review": {
                "translator": None,
                "reviewer": None,
                "glossary_version": None,
                "complete_sentence_confirmed": False,
                "notes": None,
            },
        },
        "layout": {
            "status": "not_generated",
            "strategy": "expand_and_relocate",
            "pages": [],
        },
        "translation_memory_key": duplicate_key,
        "qa": {"status": "not_run", "errors": [], "warnings": []},
    }


def collect_terms(occurrences: list[dict[str, Any]]) -> dict[str, Any]:
    speaker_counts: Counter[str] = Counter()
    term_counts: dict[str, Counter[str]] = {
        "katakana": Counter(),
        "kanji_compound": Counter(),
    }
    examples: dict[tuple[str, str], list[str]] = defaultdict(list)

    for occurrence in occurrences:
        ja = occurrence["japanese"]
        for mention in ja["speaker_mentions"]:
            speaker = mention["ja"]
            speaker_counts[speaker] += 1
            key = ("speaker", speaker)
            if len(examples[key]) < 3:
                examples[key].append(occurrence["id"])

        body = ja["plain_full"]
        for category, pattern in (
            ("katakana", KATAKANA_TERM),
            ("kanji_compound", KANJI_TERM),
        ):
            seen_in_occurrence: set[str] = set()
            for match in pattern.finditer(body):
                term = match.group(0).strip("・=")
                if len(term) < 2 or term in seen_in_occurrence:
                    continue
                seen_in_occurrence.add(term)
                term_counts[category][term] += 1
                key = (category, term)
                if len(examples[key]) < 3:
                    examples[key].append(occurrence["id"])

    def rows(category: str, counter: Counter[str], minimum: int) -> list[dict[str, Any]]:
        return [
            {
                "ja": term,
                "occurrence_count": count,
                "example_occurrence_ids": examples[(category, term)],
                "ko_approved": None,
                "allowed_variants": [],
                "status": "unreviewed",
                "notes": None,
            }
            for term, count in sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))
            if count >= minimum
        ]

    return {
        "schema": "srwcb-second-glossary-candidates-v2",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "speaker_names": "all visible speaker labels; control-only prefixes excluded",
            "katakana_terms": "distinct per occurrence; minimum 3 occurrences",
            "kanji_compounds": "distinct per occurrence; minimum 5 occurrences",
            "approval": "ko_approved must be manually reviewed before QA may enforce it",
        },
        "speaker_names": rows("speaker", speaker_counts, 1),
        "katakana_terms": rows("katakana", term_counts["katakana"], 3),
        "kanji_compounds": rows("kanji_compound", term_counts["kanji_compound"], 5),
    }


def preserve_glossary_approvals(
    generated: dict[str, Any], previous_path: Path
) -> dict[str, Any]:
    """Carry forward manual approvals when rebuilding candidate statistics.

    The exporter owns occurrence counts and examples, while the glossary review
    owns the four manual fields below.  Matching is deliberately scoped by
    category and exact Japanese spelling so a similarly spelled short token
    cannot inherit another entry's approval.
    """
    stats = {
        "previous_glossary_found": previous_path.exists(),
        "approved_rows_preserved": 0,
        "approved_rows_not_carried_forward": [],
    }
    if not previous_path.exists():
        return stats

    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    if previous.get("schema") != generated.get("schema"):
        raise ValueError(
            "refusing to merge manual glossary approvals from a different schema"
        )

    manual_fields = ("ko_approved", "allowed_variants", "status", "notes")
    approved_by_category: dict[str, dict[str, dict[str, Any]]] = {}
    for category in ("speaker_names", "katakana_terms", "kanji_compounds"):
        approved_rows = [
            row for row in previous.get(category, []) if row.get("status") == "approved"
        ]
        approved_by_ja: dict[str, dict[str, Any]] = {}
        for row in approved_rows:
            ja = row["ja"]
            if ja in approved_by_ja:
                raise ValueError(f"duplicate approved glossary row: {category}/{ja}")
            approved_by_ja[ja] = row
        approved_by_category[category] = approved_by_ja

        retained: set[str] = set()
        for row in generated[category]:
            approved = approved_by_ja.get(row["ja"])
            if approved is None:
                continue
            for field in manual_fields:
                row[field] = approved.get(field)
            retained.add(row["ja"])
            stats["approved_rows_preserved"] += 1

        stats["approved_rows_not_carried_forward"].extend(
            f"{category}/{ja}" for ja in sorted(set(approved_by_ja) - retained)
        )

    if approved_by_category and previous.get("approval"):
        approval = dict(previous["approval"])
        approval["speaker_names_approved"] = sum(
            row.get("status") == "approved" for row in generated["speaker_names"]
        )
        approval["katakana_terms_approved"] = sum(
            row.get("status") == "approved" for row in generated["katakana_terms"]
        )
        approval["kanji_compounds_approved"] = sum(
            row.get("status") == "approved" for row in generated["kanji_compounds"]
        )
        approval["approved_term_total"] = (
            approval["katakana_terms_approved"]
            + approval["kanji_compounds_approved"]
        )
        if stats["approved_rows_not_carried_forward"]:
            approval["not_carried_forward"] = stats[
                "approved_rows_not_carried_forward"
            ]
        else:
            approval.pop("not_carried_forward", None)
        generated["approval"] = approval

    return stats


def main() -> int:
    raw = INPUT.read_bytes()
    document = json.loads(raw.decode("utf-8"))
    candidates = [
        candidate
        for candidate in document["candidates"]
        if candidate.get("source_path") in TARGETS
    ]
    glyphs = load_glyph_characters()
    selected, rejected, runtime_reconstruction = select_authoritative_candidates(
        candidates, glyphs
    )
    accepted = [make_occurrence(candidate) for candidate in selected]

    duplicate_counts = Counter(row["translation_memory_key"] for row in accepted)
    for row in accepted:
        row["translation_memory_occurrences"] = duplicate_counts[
            row["translation_memory_key"]
        ]

    counts_by_source = Counter(row["source"]["path"] for row in accepted)
    source_metadata = {
        source["path"]: source
        for source in document.get("sources", [])
        if source.get("path") in TARGETS
    }
    source_files: dict[str, dict[str, Any]] = {}
    for relative in TARGETS:
        source_raw = (EXTRACTED / relative).read_bytes()
        source = source_metadata[relative]
        source_files[relative] = {
            "size": len(source_raw),
            "sha256": sha256_bytes(source_raw),
            "format": source["format"],
            "pointer_table_bytes": source.get("pointer_table_bytes"),
            "pointer_count": source.get("pointer_count"),
        }
    synthesized_count = sum(
        len(runtime_reconstruction[name]["synthesized_occurrence_ids"])
        for name in ("bmess", "dead")
    )
    rejected_counts = Counter(row["reason"] for row in rejected)
    ledger = {
        "schema": "srwcb-second-translation-ledger-v2",
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_document": {
            "path": str(INPUT.resolve()),
            "sha256": sha256_bytes(raw),
            "schema": document.get("schema"),
        },
        "source_files": source_files,
        "runtime_reconstruction": runtime_reconstruction,
        "policy": {
            "machine_translation_imported": False,
            "old_fixed_slot_translation_imported": False,
            "source_text_mutable": False,
            "translation_layout_strategy": "expand_and_relocate",
            "speaker_names_may_be_removed": False,
            "spaces_may_be_removed_to_fit": False,
            "text_may_be_truncated": False,
        },
        "statistics": {
            "input_heuristic_candidate_count": len(candidates),
            "candidate_count": len(candidates) + synthesized_count,
            "runtime_synthesized_occurrences": synthesized_count,
            "accepted_occurrences": len(accepted),
            "rejected_occurrences": len(rejected),
            "rejected_by_reason": dict(sorted(rejected_counts.items())),
            "unique_translation_memory_keys": len(duplicate_counts),
            "accepted_by_source": dict(sorted(counts_by_source.items())),
        },
        "rejected_source_records": rejected,
        "occurrences": accepted,
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    glossary = collect_terms(accepted)
    glossary_approval_preservation = preserve_glossary_approvals(glossary, GLOSSARY)
    GLOSSARY.write_text(
        json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "ledger": str(LEDGER),
                "glossary": str(GLOSSARY),
                **ledger["statistics"],
                "glossary_counts": {
                    "speaker_names": len(glossary["speaker_names"]),
                    "katakana_terms": len(glossary["katakana_terms"]),
                    "kanji_compounds": len(glossary["kanji_compounds"]),
                },
                "glossary_approval_preservation": glossary_approval_preservation,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
