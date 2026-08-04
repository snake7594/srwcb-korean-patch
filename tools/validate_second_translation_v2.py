#!/usr/bin/env python3
"""Validate the lossless SECOND translation ledger before any binary build."""

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
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from analyze_second_message_archives import parse_bmess, parse_dead


ROOT = _P.WORK
DEFAULT_LEDGER = (
    _P.WORK
    / "research"
    / "translation_v2"
    / "second_translation_ledger.json"
)
DEFAULT_GLOSSARY = (
    _P.WORK
    / "research"
    / "translation_v2"
    / "glossary_candidates.json"
)
DEFAULT_EXTRACTED = _P.EXTRACTED

JAPANESE_RE = re.compile(r"[\u3040-\u30ff]")
HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
TERMINAL_RE = re.compile(r"(?:[.!?。！？…‥]+|[.!?。！？…‥]+[」』”’])$")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def control_tag(part: dict[str, Any]) -> str:
    opcode = part["opcode"][2:]
    args = part["args_hex"]
    return f"⟦{opcode}:{args}⟧" if args else f"⟦{opcode}⟧"


def source_layout_text(parts: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for part in parts:
        if part["kind"] == "text":
            out.append(part["ja"])
        elif part["kind"] == "line_break":
            out.append("\n")
        else:
            out.append(control_tag(part))
    return "".join(out)


def semantic_source_text(parts: list[dict[str, Any]]) -> str:
    out: list[str] = []
    for part in parts:
        if part["kind"] == "text":
            out.append(part["ja"])
        else:
            out.append(control_tag(part))
    return "".join(out)


def korean_visible_text(row: dict[str, Any]) -> str | None:
    values = row["translation"]["ko_parts"]
    out: list[str] = []
    for part in row["japanese"]["translation_parts"]:
        if part["kind"] != "text":
            continue
        value = values.get(part["part_id"])
        if not isinstance(value, str):
            return None
        out.append(value)
    return "".join(out)


def approved_glossary(path: Path) -> tuple[dict[str, str], list[dict[str, Any]]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    speakers: dict[str, str] = {}
    terms: list[dict[str, Any]] = []
    for row in data.get("speaker_names", []):
        if row.get("status") == "approved" and row.get("ko_approved"):
            speakers[row["ja"]] = row["ko_approved"]
    for category in ("katakana_terms", "kanji_compounds"):
        for row in data.get(category, []):
            if row.get("status") == "approved" and row.get("ko_approved"):
                terms.append(row)
    return speakers, terms


def validate(
    ledger_path: Path,
    glossary_path: Path,
    extracted_root: Path,
) -> dict[str, Any]:
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    glossary_speakers, glossary_terms = approved_glossary(glossary_path)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []

    def error(code: str, row_id: str, message: str) -> None:
        errors.append({"code": code, "id": row_id, "message": message})

    def warn(code: str, row_id: str, message: str) -> None:
        warnings.append({"code": code, "id": row_id, "message": message})

    source_data: dict[str, bytes] = {}
    for relative, expected in ledger["source_files"].items():
        path = extracted_root / relative
        if not path.exists():
            error("SOURCE_FILE_MISSING", relative, str(path))
            continue
        raw = path.read_bytes()
        source_data[relative] = raw
        if len(raw) != expected["size"]:
            error(
                "SOURCE_SIZE_CHANGED",
                relative,
                f"expected {expected['size']}, found {len(raw)}",
            )
        actual_sha = sha256_bytes(raw)
        if actual_sha != expected["sha256"]:
            error(
                "SOURCE_SHA_CHANGED",
                relative,
                f"expected {expected['sha256']}, found {actual_sha}",
            )

    runtime_expected: dict[str, dict[int, tuple[int, dict[str, Any]]]] = {
        "BMESS2.BIN": {},
        "SECOND/2_DEAD.BIN": {},
    }
    runtime_counts: dict[str, int] = {}
    try:
        bmess = parse_bmess(source_data["BMESS2.BIN"])
        for block in bmess.blocks:
            for target, record in block.text_records.items():
                start = block.file_start + 15 + target
                reference_fields = list(block.text_references[target])
                runtime_expected["BMESS2.BIN"][start] = (
                    block.file_start + 15 + record.end,
                    {
                        "format": "bmess_block_leaf",
                        "block_index": block.index,
                        "payload_text_offset": target,
                        "payload_text_offset_hex": f"0x{target:X}",
                        "leaf_reference_fields": reference_fields,
                        "leaf_reference_count": len(reference_fields),
                    },
                )
        runtime_counts["bmess_live"] = len(runtime_expected["BMESS2.BIN"])
        runtime_counts["bmess_unreferenced_quoted"] = sum(
            len(block.unreferenced_quoted_records) for block in bmess.blocks
        )
        runtime_counts["bmess_leaf_references"] = sum(
            len(fields)
            for block in bmess.blocks
            for fields in block.text_references.values()
        )
    except (KeyError, ValueError) as exc:
        error("BMESS_RUNTIME_PARSE", "BMESS2.BIN", str(exc))

    try:
        dead = parse_dead(source_data["SECOND/2_DEAD.BIN"])
        live_slots = {
            start: (slot_index, end)
            for slot_index, (start, end) in enumerate(dead.slots)
            if start != end
        }
        for start, record in dead.records.items():
            slot_index, end = live_slots[start]
            runtime_expected["SECOND/2_DEAD.BIN"][start] = (
                end,
                {
                    "format": "dead_start_end_slot",
                    "slot_index": slot_index,
                    "start_pointer_index": slot_index * 2,
                    "end_pointer_index": slot_index * 2 + 1,
                },
            )
        runtime_counts["dead_live"] = len(dead.records)
        runtime_counts["dead_empty_slots"] = sum(
            start == end for start, end in dead.slots
        )
    except (KeyError, ValueError) as exc:
        error("DEAD_RUNTIME_PARSE", "SECOND/2_DEAD.BIN", str(exc))

    reconstruction = ledger.get("runtime_reconstruction", {})
    reported_bmess = reconstruction.get("bmess", {})
    reported_dead = reconstruction.get("dead", {})
    if "BMESS2.BIN" in source_data and reported_bmess.get("source_sha256") != sha256_bytes(
        source_data["BMESS2.BIN"]
    ):
        error(
            "BMESS_RUNTIME_SOURCE_SHA",
            "runtime_reconstruction",
            "BMESS runtime source hash does not match extracted source",
        )
    if "SECOND/2_DEAD.BIN" in source_data and reported_dead.get(
        "source_sha256"
    ) != sha256_bytes(source_data["SECOND/2_DEAD.BIN"]):
        error(
            "DEAD_RUNTIME_SOURCE_SHA",
            "runtime_reconstruction",
            "2_DEAD runtime source hash does not match extracted source",
        )
    reported_checks = (
        ("BMESS_LIVE_COUNT", reported_bmess.get("live_text_record_count"), runtime_counts.get("bmess_live")),
        ("BMESS_UNREFERENCED_COUNT", reported_bmess.get("unreferenced_quoted_record_count"), runtime_counts.get("bmess_unreferenced_quoted")),
        ("BMESS_REFERENCE_COUNT", reported_bmess.get("leaf_reference_count"), runtime_counts.get("bmess_leaf_references")),
        ("DEAD_LIVE_COUNT", reported_dead.get("live_text_record_count"), runtime_counts.get("dead_live")),
        ("DEAD_EMPTY_COUNT", reported_dead.get("empty_slot_count"), runtime_counts.get("dead_empty_slots")),
    )
    for code, reported, actual in reported_checks:
        if reported != actual:
            error(code, "runtime_reconstruction", f"reported {reported}, parsed {actual}")

    seen_ids: set[str] = set()
    ranges: dict[str, list[tuple[int, int, str]]] = defaultdict(list)
    ledger_runtime_offsets: dict[str, set[int]] = {
        relative: set() for relative in runtime_expected
    }
    approved_by_memory: dict[str, set[tuple[str | None, str]]] = defaultdict(set)

    for row in ledger["occurrences"]:
        row_id = row["id"]
        if row_id in seen_ids:
            error("DUPLICATE_ID", row_id, "occurrence ID is not unique")
        seen_ids.add(row_id)

        source = row["source"]
        relative = source["path"]
        start = source["offset"]
        end = source["end_offset_exclusive"]
        ranges[relative].append((start, end, row_id))
        if end - start != source["byte_length"]:
            error("SOURCE_LENGTH_METADATA", row_id, "source byte length is inconsistent")
        try:
            expected_raw = bytes.fromhex(source["raw_hex"])
        except ValueError:
            error("SOURCE_RAW_HEX", row_id, "raw_hex is invalid")
            expected_raw = b""
        if sha256_bytes(expected_raw) != source["raw_sha256"]:
            error("SOURCE_RECORD_SHA_METADATA", row_id, "raw_hex SHA-256 differs")
        if relative in source_data:
            actual_raw = source_data[relative][start:end]
            if actual_raw != expected_raw:
                error(
                    "SOURCE_RECORD_CHANGED",
                    row_id,
                    f"{relative} at {source['offset_hex']} differs from ledger",
                )

        if relative in runtime_expected:
            if start in ledger_runtime_offsets[relative]:
                error(
                    "RUNTIME_TARGET_DUPLICATE",
                    row_id,
                    f"{relative} offset {start:#x} appears more than once",
                )
            ledger_runtime_offsets[relative].add(start)
            expected_runtime = runtime_expected[relative].get(start)
            if expected_runtime is None:
                error(
                    "RUNTIME_TARGET_UNREFERENCED",
                    row_id,
                    f"{relative} offset {start:#x} is not a live runtime text target",
                )
            else:
                expected_end, expected_reference = expected_runtime
                if end != expected_end:
                    error(
                        "RUNTIME_TARGET_LENGTH",
                        row_id,
                        f"runtime record ends at {expected_end:#x}, ledger ends at {end:#x}",
                    )
                if source.get("runtime_reference") != expected_reference:
                    error(
                        "RUNTIME_REFERENCE_METADATA",
                        row_id,
                        "runtime_reference differs from independently parsed pointers",
                    )
        elif source.get("runtime_reference") is not None:
            error(
                "RUNTIME_REFERENCE_UNEXPECTED",
                row_id,
                "scenario record must not claim a BMESS/DEAD runtime reference",
            )

        japanese = row["japanese"]
        rebuilt_layout = source_layout_text(japanese["source_layout_parts"])
        if rebuilt_layout != japanese["normalised_full"]:
            error("SOURCE_LAYOUT_ROUNDTRIP", row_id, "source layout parts changed text")
        rebuilt_semantic = semantic_source_text(japanese["translation_parts"])
        if rebuilt_semantic != japanese["normalised_full"].replace("\n", ""):
            error(
                "SOURCE_SEMANTIC_ROUNDTRIP",
                row_id,
                "translation parts changed Japanese body or control anchors",
            )

        source_part_ids = [
            part["part_id"]
            for part in japanese["translation_parts"]
            if part["kind"] == "text"
        ]
        ko_parts = row["translation"]["ko_parts"]
        if set(ko_parts) != set(source_part_ids):
            error(
                "TRANSLATION_PART_KEYS",
                row_id,
                "ko_parts keys differ from immutable Japanese text part IDs",
            )

        status = row["translation"]["status"]
        ko_text = korean_visible_text(row)
        if status == "untranslated":
            if any(value not in (None, "") for value in ko_parts.values()):
                warn(
                    "UNTRANSLATED_HAS_TEXT",
                    row_id,
                    "translation text exists but status is untranslated",
                )
            continue

        if ko_text is None or any(not value for value in ko_parts.values()):
            error("TRANSLATION_PART_EMPTY", row_id, "one or more Korean parts are empty")
            continue

        part_targets = row["translation"]["ko_parts"]
        speakers_for_memory: list[str] = []
        mentions_by_part: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for mention in japanese["speaker_mentions"]:
            mentions_by_part[mention["part_id"]].append(mention)
        target_speaker_pattern = re.compile(
            r"(?:^|(?<=」))([　 ]*)([^「」\n]{1,20})「"
        )
        for part_id, mentions in mentions_by_part.items():
            part_text = part_targets.get(part_id)
            if not isinstance(part_text, str):
                continue
            target_mentions = list(target_speaker_pattern.finditer(part_text))
            if len(target_mentions) != len(mentions):
                error(
                    "SPEAKER_MISSING",
                    row_id,
                    f"part {part_id} expected {len(mentions)} speaker labels, "
                    f"found {len(target_mentions)}",
                )
                continue
            for mention, match in zip(mentions, target_mentions):
                speaker_ko = match.group(2).strip()
                speakers_for_memory.append(speaker_ko)
                if status == "approved":
                    approved = glossary_speakers.get(mention["ja"])
                    if approved is None:
                        error(
                            "SPEAKER_GLOSSARY_UNAPPROVED",
                            row_id,
                            f"speaker {mention['ja']!r} has no approved glossary spelling",
                        )
                    elif speaker_ko != approved:
                        error(
                            "SPEAKER_GLOSSARY_MISMATCH",
                            row_id,
                            f"expected {approved!r}, found {speaker_ko!r}",
                        )

        for part in japanese["translation_parts"]:
            if part["kind"] != "text":
                continue
            target = part_targets.get(part["part_id"])
            if not isinstance(target, str):
                continue
            if part["ja"].count("「") != target.count("「"):
                error(
                    "OPEN_QUOTE_COUNT_CHANGED",
                    row_id,
                    f"part {part['part_id']} changed opening quote count",
                )
            if part["ja"].count("」") != target.count("」"):
                error(
                    "CLOSE_QUOTE_COUNT_CHANGED",
                    row_id,
                    f"part {part['part_id']} changed closing quote count",
                )

        if JAPANESE_RE.search(ko_text):
            error("JAPANESE_REMAINS", row_id, "Korean text still contains Japanese")
        if "⟦F" in ko_text:
            error(
                "CONTROL_TAG_IN_TRANSLATION",
                row_id,
                "translator inserted a control tag into a text part",
            )

        hangul_count = len(HANGUL_RE.findall(ko_text))
        if hangul_count >= 12 and not re.search(r"\s", ko_text):
            error(
                "KOREAN_SPACING_MISSING",
                row_id,
                "12+ Hangul syllables have no whitespace",
            )

        source_plain = japanese["plain_full"].strip()
        if TERMINAL_RE.search(source_plain) and not TERMINAL_RE.search(ko_text.strip()):
            error(
                "SENTENCE_END_MISSING",
                row_id,
                "source has terminal punctuation but Korean text does not",
            )

        source_chars = max(
            1,
            len(re.sub(r"[\s\u3000.!?。！？…‥「」]", "", source_plain)),
        )
        ko_chars = len(re.sub(r"[\s.!?。！？…「」]", "", ko_text))
        ratio = ko_chars / source_chars
        if ratio < 0.45 or ratio > 2.2:
            warn(
                "LENGTH_RATIO_REVIEW",
                row_id,
                f"Korean/Japanese visible character ratio is {ratio:.2f}",
            )

        for term in glossary_terms:
            if term["ja"] not in source_plain:
                continue
            accepted = [term["ko_approved"], *term.get("allowed_variants", [])]
            if not any(value and value in ko_text for value in accepted):
                error(
                    "GLOSSARY_TERM_MISSING",
                    row_id,
                    f"{term['ja']!r} requires one of {accepted!r}",
                )

        review = row["translation"]["review"]
        if status == "approved":
            if not review.get("translator"):
                error("APPROVAL_TRANSLATOR_MISSING", row_id, "translator is empty")
            if not review.get("reviewer"):
                error("APPROVAL_REVIEWER_MISSING", row_id, "reviewer is empty")
            if review.get("translator") == review.get("reviewer"):
                error(
                    "APPROVAL_NOT_INDEPENDENT",
                    row_id,
                    "translator and reviewer must differ",
                )
            if not review.get("glossary_version"):
                error("APPROVAL_GLOSSARY_MISSING", row_id, "glossary version is empty")
            if review.get("complete_sentence_confirmed") is not True:
                error(
                    "APPROVAL_SENTENCE_UNCONFIRMED",
                    row_id,
                    "sentence completeness was not confirmed",
                )
            approved_by_memory[row["translation_memory_key"]].add(
                (" / ".join(speakers_for_memory) or None, ko_text)
            )

        layout = row["layout"]
        if layout["status"] in {"generated", "verified"}:
            lines: list[str] = []
            for page in layout["pages"]:
                page_lines = page.get("lines", [])
                if not 1 <= len(page_lines) <= 3:
                    error(
                        "LAYOUT_PAGE_LINE_COUNT",
                        row_id,
                        "each page must contain 1 to 3 lines",
                    )
                for line in page_lines:
                    if len(line) > 26:
                        warn(
                            "LAYOUT_LINE_OVER_26",
                            row_id,
                            f"line has {len(line)} cells",
                        )
                    lines.append(line)
            if "".join(lines) != ko_text:
                error(
                    "LAYOUT_TEXT_CHANGED",
                    row_id,
                    "removing page/line breaks does not reproduce approved text exactly",
                )
        if status == "approved" and layout["strategy"] != "expand_and_relocate":
            error(
                "FIXED_SLOT_FORBIDDEN",
                row_id,
                "approved translation must use expansion and relocation",
            )

    for relative, expected_records in runtime_expected.items():
        expected_offsets = set(expected_records)
        actual_offsets = ledger_runtime_offsets[relative]
        missing = sorted(expected_offsets - actual_offsets)
        extra = sorted(actual_offsets - expected_offsets)
        if missing:
            sample = ", ".join(f"{offset:#x}" for offset in missing[:8])
            error(
                "RUNTIME_TARGETS_MISSING",
                relative,
                f"{len(missing)} live records absent from ledger; first: {sample}",
            )
        if extra:
            sample = ", ".join(f"{offset:#x}" for offset in extra[:8])
            error(
                "RUNTIME_TARGETS_EXTRA",
                relative,
                f"{len(extra)} non-live records present in ledger; first: {sample}",
            )

    for relative, items in ranges.items():
        # BMESS legitimately contains nested live records whose quoted suffixes
        # share source bytes. Exact pointer-set validation above is authoritative.
        if relative == "BMESS2.BIN":
            continue
        items.sort()
        for left, right in zip(items, items[1:]):
            if left[1] > right[0]:
                error(
                    "SOURCE_RECORD_OVERLAP",
                    f"{left[2]} / {right[2]}",
                    f"overlap in {relative}",
                )

    for memory_key, translations in approved_by_memory.items():
        if len(translations) > 1:
            warn(
                "TRANSLATION_MEMORY_INCONSISTENT",
                memory_key,
                f"{len(translations)} approved translations for the same source",
            )

    return {
        "schema": "srwcb-second-translation-qa-v2",
        "ledger": str(ledger_path.resolve()),
        "errors": errors,
        "warnings": warnings,
        "runtime_reference_summary": {
            "bmess_live_records": runtime_counts.get("bmess_live"),
            "bmess_leaf_references": runtime_counts.get("bmess_leaf_references"),
            "bmess_unreferenced_quoted_records": runtime_counts.get(
                "bmess_unreferenced_quoted"
            ),
            "dead_live_records": runtime_counts.get("dead_live"),
            "dead_empty_slots": runtime_counts.get("dead_empty_slots"),
        },
        "summary": {
            "occurrences": len(ledger["occurrences"]),
            "errors": len(errors),
            "warnings": len(warnings),
            "passed": not errors,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--glossary", type=Path, default=DEFAULT_GLOSSARY)
    parser.add_argument("--extracted-root", type=Path, default=DEFAULT_EXTRACTED)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    report = validate(args.ledger, args.glossary, args.extracted_root)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.write_text(rendered, encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
