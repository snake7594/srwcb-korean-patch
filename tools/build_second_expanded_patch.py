#!/usr/bin/env python3
"""Build the variable-length SECOND Korean patch.

This builder has no import or fallback path to the rejected fixed-slot/Google
translation cache.  It rebuilds all three runtime message containers, moves
the enlarged files to fresh ISO extents, patches any additional required font
glyphs, and can produce and decode-verify the final xdelta.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import struct
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from analyze_second_message_archives import (  # noqa: E402
    BMESS_RUNTIME_SLOT_BYTES,
    parse_bmess,
    parse_dead,
    rebuild_bmess_repack,
    rebuild_dead,
)
from build_exe_hangul_font import (  # noqa: E402
    EXE_LAYOUT as FONT_EXE_LAYOUT,
    FONT_BYTES,
    GLYPH_BYTES,
    parse_bdf,
    render_glyph,
)
from patch_raw_track_exes import (  # noqa: E402
    EXE_LAYOUT as TRACK_EXE_LAYOUT,
    SECTOR_SIZE,
    USER_DATA_OFFSET,
    USER_DATA_SIZE,
    patch_one_executable,
    rebuild_mode2_form1,
)
from rebuild_second_sce import rebuild_second_sce  # noqa: E402
from relocate_expanded_iso_files import (  # noqa: E402
    find_directory_entry_stream,
    relocate_files,
)
from second_translation_codec import (  # noqa: E402
    EXTRA_GLYPH_START,
    add_extra_glyph_mapping,
    assemble_translated_record,
    load_safe_glyph_map,
    normalise_for_font,
    required_extra_characters,
)


ROOT = Path(__file__).resolve().parents[1]
EXTRACTED = ROOT / "extracted"
SAFE_BUILD = ROOT / "test_build" / "exe_font_safe_test"
SAFE_EXTRACTED = SAFE_BUILD / "extracted"
SAFE_FONT = SAFE_BUILD / "font" / "srwcb_font_hangul_test_2816_16x16.bin"
SAFE_TRACK = SAFE_BUILD / "Super Robot Taisen Complete Box Hangul Safe Font Test (Track 1).bin"
ORIGINAL_TRACK = ROOT / "Super Robot Taisen Complete Box (Track 1).bin"
BDF = ROOT / "font" / "Galmuri14.bdf"
# The source ledger contains reconstructed original-game records and is
# intentionally not distributed. Generate it locally from a legally owned
# disc, or pass an explicit path with --ledger.
LEDGER = ROOT / "local_inputs" / "second_translation_ledger.json"
OVERLAY = ROOT / "translation" / "second_translation_overlay.json"
DEFAULT_OUTPUT = ROOT / "test_build" / "second_korean_v0.1.0-pre"
XDELTA = ROOT / "xdelta.exe"

SOURCE_SCE = EXTRACTED / "SECOND" / "2_SCE.BIN"
SOURCE_BMESS = EXTRACTED / "BMESS2.BIN"
SOURCE_DEAD = EXTRACTED / "SECOND" / "2_DEAD.BIN"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_translation_inputs(
    ledger_path: Path,
    overlay_path: Path,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, Any]]:
    ledger_doc = json.loads(ledger_path.read_text(encoding="utf-8"))
    overlay_doc = json.loads(overlay_path.read_text(encoding="utf-8"))
    if overlay_doc.get("schema") != "srwcb-second-translation-overlay-v2":
        raise ValueError("unexpected translation overlay schema")
    stats = overlay_doc.get("statistics", {})
    if stats.get("partial"):
        raise ValueError("translation overlay is partial")
    rows = ledger_doc["occurrences"]
    translations = overlay_doc["translations"]
    expected_keys = {row["translation_memory_key"] for row in rows}
    if set(translations) != expected_keys:
        missing = sorted(expected_keys - set(translations))
        extra = sorted(set(translations) - expected_keys)
        raise ValueError(
            f"translation key mismatch: {len(missing)} missing, {len(extra)} extra; "
            f"examples missing={missing[:3]}, extra={extra[:3]}"
        )
    for row in rows:
        key = row["translation_memory_key"]
        expected_parts = {
            part["part_id"] for part in row["japanese"]["translation_parts"]
            if part["kind"] == "text"
        }
        ko_parts = translations[key].get("ko_parts", {})
        if set(ko_parts) != expected_parts or any(
            not isinstance(value, str) or not value.strip() for value in ko_parts.values()
        ):
            raise ValueError(f"{row['id']}: incomplete Korean text parts")
    return rows, translations, ledger_doc


def verify_source_row(row: dict[str, Any], source: bytes) -> bytes:
    info = row["source"]
    start = info["offset"]
    end = info["end_offset_exclusive"]
    raw = source[start:end]
    expected = bytes.fromhex(info["raw_hex"])
    if raw != expected:
        raise ValueError(f"{row['id']}: pristine source bytes changed")
    if sha256_bytes(raw) != info["raw_sha256"]:
        raise ValueError(f"{row['id']}: source record SHA-256 changed")
    return raw


def build_dynamic_font(
    extra_characters: list[str],
    output_dir: Path,
) -> tuple[dict[str, int], dict[str, Any], Path | None]:
    base_map = load_safe_glyph_map()
    glyph_map = add_extra_glyph_mapping(base_map, extra_characters)
    if not extra_characters:
        return glyph_map, {"extra_glyph_count": 0, "characters": []}, None

    glyphs = parse_bdf(BDF)
    missing_bdf = [char for char in extra_characters if ord(char) not in glyphs]
    if missing_bdf:
        rendered = " ".join(f"{char}(U+{ord(char):04X})" for char in missing_bdf)
        raise ValueError(f"Galmuri14 BDF has no glyph for: {rendered}")

    safe_font = SAFE_FONT.read_bytes()
    if len(safe_font) != FONT_BYTES:
        raise ValueError("safe injected font has an unexpected size")
    patched_font = bytearray(safe_font)
    rows: list[dict[str, Any]] = []
    for ordinal, char in enumerate(extra_characters):
        index = EXTRA_GLYPH_START + ordinal
        bitmap = render_glyph(glyphs[ord(char)])
        start = index * GLYPH_BYTES
        patched_font[start:start + GLYPH_BYTES] = bitmap
        rows.append(
            {
                "character": char,
                "unicode": f"U+{ord(char):04X}",
                "glyph_index": index,
                "message_bytes": encode_index(index).hex(" ").upper(),
            }
        )

    font_dir = output_dir / "font"
    font_dir.mkdir(parents=True, exist_ok=True)
    full_font_path = font_dir / "srwcb_font_hangul_dynamic_2816_16x16.bin"
    full_font_path.write_bytes(patched_font)
    write_json(font_dir / "extra_glyph_mapping.json", rows)

    dynamic_extracted = output_dir / "font_extracted"
    for relative_path, font_offset in FONT_EXE_LAYOUT.items():
        source_path = SAFE_EXTRACTED / relative_path
        source = source_path.read_bytes()
        if source[font_offset:font_offset + FONT_BYTES] != safe_font:
            raise ValueError(f"{source_path}: safe font table mismatch")
        patched = bytearray(source)
        patched[font_offset:font_offset + FONT_BYTES] = patched_font
        destination = dynamic_extracted / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(patched)

    return glyph_map, {
        "extra_glyph_count": len(extra_characters),
        "characters": rows,
        "safe_font_sha256": sha256_bytes(safe_font),
        "dynamic_font_sha256": sha256_bytes(bytes(patched_font)),
    }, dynamic_extracted


def encode_index(index: int) -> bytes:
    if index < 0xEB:
        return bytes((index,))
    return bytes((0xEB + (index >> 8), index & 0xFF))


def make_replacements(
    rows: list[dict[str, Any]],
    translations: dict[str, dict[str, Any]],
    glyph_map: dict[str, int],
    source_sce: bytes,
    source_bmess: bytes,
    source_dead: bytes,
) -> tuple[dict[int, bytes], dict[tuple[int, int], bytes], dict[int, bytes], list[dict[str, Any]]]:
    bm_archive = parse_bmess(source_bmess)
    bm_by_absolute = {
        block.file_start + 15 + target: (block.index, target)
        for block in bm_archive.blocks
        for target in block.text_records
    }
    dead_archive = parse_dead(source_dead)
    dead_starts = set(dead_archive.records)

    sce_replacements: dict[int, bytes] = {}
    bm_replacements: dict[tuple[int, int], bytes] = {}
    dead_replacements: dict[int, bytes] = {}
    manifest: list[dict[str, Any]] = []
    sources = {
        "scenario": source_sce,
        "battle_message": source_bmess,
        "death_quote": source_dead,
    }

    for row in rows:
        kind = row["kind"]
        source = sources[kind]
        old_raw = verify_source_row(row, source)
        key = row["translation_memory_key"]
        ko_parts = translations[key]["ko_parts"]
        encoded, layout = assemble_translated_record(
            row["japanese"]["translation_parts"], ko_parts, glyph_map
        )
        offset = row["source"]["offset"]
        if kind == "scenario":
            if offset in sce_replacements:
                raise ValueError(f"duplicate SCE record offset {offset:#x}")
            sce_replacements[offset] = encoded
            target: Any = offset
        elif kind == "battle_message":
            if offset not in bm_by_absolute:
                raise ValueError(
                    f"{row['id']}: record is not a live BMESS graph target; "
                    "unreferenced strings must not be patched"
                )
            target = bm_by_absolute[offset]
            if target in bm_replacements:
                raise ValueError(f"duplicate BMESS target {target}")
            bm_replacements[target] = encoded
        elif kind == "death_quote":
            if offset not in dead_starts:
                raise ValueError(f"{row['id']}: record is not a live DEAD slot")
            if offset in dead_replacements:
                raise ValueError(f"duplicate DEAD record offset {offset:#x}")
            dead_replacements[offset] = encoded
            target = offset
        else:
            raise ValueError(f"unknown ledger kind {kind!r}")

        manifest.append(
            {
                "id": row["id"],
                "kind": kind,
                "translation_memory_key": key,
                "source_offset": offset,
                "container_target": target,
                "source_length": len(old_raw),
                "output_length": len(encoded),
                "byte_delta": len(encoded) - len(old_raw),
                "source_sha256": sha256_bytes(old_raw),
                "output_sha256": sha256_bytes(encoded),
                "ko_parts": ko_parts,
                "layout": layout,
            }
        )
    return sce_replacements, bm_replacements, dead_replacements, manifest


def patch_dynamic_font_track(track_path: Path, dynamic_extracted: Path | None) -> list[dict[str, Any]]:
    if dynamic_extracted is None:
        return []
    manifest: list[dict[str, Any]] = []
    with track_path.open("r+b") as track:
        for relative_path, lba in TRACK_EXE_LAYOUT.items():
            source = (SAFE_EXTRACTED / relative_path).read_bytes()
            patched = (dynamic_extracted / relative_path).read_bytes()
            patch_one_executable(track, lba, source, patched, str(relative_path))
            manifest.append(
                {
                    "path": str(relative_path).replace("\\", "/"),
                    "lba": lba,
                    "sha256": sha256_bytes(patched),
                }
            )
    return manifest


def read_iso_file(track_path: Path, iso_name: str) -> tuple[bytes, int, int, list[bytes]]:
    with track_path.open("rb") as track:
        entry = find_directory_entry_stream(track, iso_name.encode("ascii"))
        track.seek(entry + 2)
        fields = track.read(16)
    lba = struct.unpack_from("<I", fields, 0)[0]
    if struct.unpack_from(">I", fields, 4)[0] != lba:
        raise AssertionError(f"{iso_name}: big/little LBA differ")
    size = struct.unpack_from("<I", fields, 8)[0]
    if struct.unpack_from(">I", fields, 12)[0] != size:
        raise AssertionError(f"{iso_name}: big/little size differ")
    sectors: list[bytes] = []
    payload = bytearray()
    with track_path.open("rb") as track:
        for index in range(math.ceil(size / USER_DATA_SIZE)):
            track.seek((lba + index) * SECTOR_SIZE)
            sector = track.read(SECTOR_SIZE)
            if len(sector) != SECTOR_SIZE:
                raise AssertionError(f"{iso_name}: extent runs past physical track")
            sectors.append(sector)
            payload.extend(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
    return bytes(payload[:size]), lba, size, sectors


def verify_sector(sector: bytes) -> None:
    rebuilt = bytearray(sector)
    rebuild_mode2_form1(rebuilt)
    if bytes(rebuilt) != sector:
        raise AssertionError("MODE2 Form1 EDC/ECC verification failed")


def verify_final_track(
    track_path: Path,
    patched_files: dict[str, bytes],
    dynamic_extracted: Path | None,
) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for iso_name, expected in patched_files.items():
        actual, lba, size, sectors = read_iso_file(track_path, iso_name)
        if actual != expected:
            raise AssertionError(f"{iso_name}: final ISO extent content mismatch")
        for sector in sectors:
            verify_sector(sector)
        files.append(
            {
                "iso_name": iso_name,
                "lba": lba,
                "size": size,
                "sha256": sha256_bytes(actual),
                "sector_count": len(sectors),
                "edc_ecc_verified": True,
            }
        )

    executable_checks: list[dict[str, Any]] = []
    expected_root = dynamic_extracted or SAFE_EXTRACTED
    with track_path.open("rb") as track:
        for relative_path, lba in TRACK_EXE_LAYOUT.items():
            expected = (expected_root / relative_path).read_bytes()
            actual = bytearray()
            for index in range(len(expected) // USER_DATA_SIZE):
                track.seek((lba + index) * SECTOR_SIZE)
                sector = track.read(SECTOR_SIZE)
                actual.extend(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
            if bytes(actual) != expected:
                raise AssertionError(f"{relative_path}: final executable/font mismatch")
            executable_checks.append(
                {
                    "path": str(relative_path).replace("\\", "/"),
                    "lba": lba,
                    "sha256": sha256_bytes(expected),
                }
            )
    return {"files": files, "executables": executable_checks}


def create_and_verify_xdelta(
    original_track: Path,
    patched_track: Path,
    xdelta_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    if not xdelta_path.exists():
        raise ValueError(f"xdelta executable not found: {xdelta_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    subprocess.run(
        [str(xdelta_path), "-9", "-e", "-s", str(original_track), str(patched_track), str(output_path)],
        check=True,
    )
    decoded = output_path.with_suffix(".decoded.bin")
    if decoded.exists():
        decoded.unlink()
    subprocess.run(
        [str(xdelta_path), "-d", "-s", str(original_track), str(output_path), str(decoded)],
        check=True,
    )
    patched_hash = sha256_file(patched_track)
    decoded_hash = sha256_file(decoded)
    if decoded_hash != patched_hash:
        raise AssertionError("decoded xdelta output does not match patched track")
    decoded.unlink()
    return {
        "path": str(output_path),
        "size": output_path.stat().st_size,
        "sha256": sha256_file(output_path),
        "decoded_sha256": decoded_hash,
        "decoded_matches_patched_track": True,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    ap.add_argument("--overlay", type=Path, default=OVERLAY)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--safe-track", type=Path, default=SAFE_TRACK)
    ap.add_argument("--original-track", type=Path, default=ORIGINAL_TRACK)
    ap.add_argument("--xdelta", type=Path, default=XDELTA)
    ap.add_argument("--skip-track", action="store_true", help="build and verify archives only")
    ap.add_argument("--skip-xdelta", action="store_true")
    args = ap.parse_args()

    rows, translations, ledger_doc = validate_translation_inputs(args.ledger, args.overlay)
    normalised_texts = [
        normalise_for_font(value)[0]
        for translation in translations.values()
        for value in translation["ko_parts"].values()
    ]
    base_map = load_safe_glyph_map()
    extra_characters = required_extra_characters(normalised_texts, base_map)
    glyph_map, font_manifest, dynamic_extracted = build_dynamic_font(
        extra_characters, args.output_dir
    )

    source_sce = SOURCE_SCE.read_bytes()
    source_bmess = SOURCE_BMESS.read_bytes()
    source_dead = SOURCE_DEAD.read_bytes()
    sce_repl, bm_repl, dead_repl, record_manifest = make_replacements(
        rows, translations, glyph_map, source_sce, source_bmess, source_dead
    )

    rebuilt_sce, sce_manifest = rebuild_second_sce(source_sce, sce_repl)
    rebuilt_bmess = rebuild_bmess_repack(source_bmess, bm_repl)
    rebuilt_dead = rebuild_dead(source_dead, dead_repl)
    parsed_bmess = parse_bmess(rebuilt_bmess)
    parsed_dead = parse_dead(rebuilt_dead)

    rebuilt_root = args.output_dir / "rebuilt"
    out_sce = rebuilt_root / "SECOND" / "2_SCE.BIN"
    out_bmess = rebuilt_root / "BMESS2.BIN"
    out_dead = rebuilt_root / "SECOND" / "2_DEAD.BIN"
    out_sce.parent.mkdir(parents=True, exist_ok=True)
    out_dead.parent.mkdir(parents=True, exist_ok=True)
    out_bmess.parent.mkdir(parents=True, exist_ok=True)
    out_sce.write_bytes(rebuilt_sce)
    out_bmess.write_bytes(rebuilt_bmess)
    out_dead.write_bytes(rebuilt_dead)

    detailed_manifest = args.output_dir / "translation_build_records.json"
    write_json(detailed_manifest, record_manifest)
    layout_counts = Counter()
    for record in record_manifest:
        layout = record["layout"]
        layout_counts["pages"] += layout["page_count"]
        layout_counts["inserted_line_breaks"] += layout["inserted_line_breaks"]
        layout_counts["inserted_page_breaks"] += layout["inserted_page_breaks"]
        layout_counts["preserved_page_breaks"] += layout["preserved_page_breaks"]
        layout_counts["normalised_characters"] += len(layout["normalisation"])

    manifest: dict[str, Any] = {
        "format": "srwcb-second-expanded-korean-v2",
        "translation_policy": {
            "legacy_translation_reused": False,
            "fixed_slot_compaction": False,
            "speaker_names_preserved": True,
            "normal_korean_spacing_required": True,
            "truncation_allowed": False,
            "record_strategy": "variable-length rebuild and ISO relocation",
        },
        "ledger": {
            "path": str(args.ledger),
            "sha256": sha256_file(args.ledger),
            "occurrences": len(rows),
            "unique_translation_keys": len(translations),
            "source_statistics": ledger_doc.get("statistics"),
        },
        "overlay": {
            "path": str(args.overlay),
            "sha256": sha256_file(args.overlay),
            "unique_translation_keys": len(translations),
            "partial": False,
        },
        "font": font_manifest,
        "records": {
            "scenario": len(sce_repl),
            "battle_message": len(bm_repl),
            "death_quote": len(dead_repl),
            "detailed_manifest": str(detailed_manifest),
            "detailed_manifest_sha256": sha256_file(detailed_manifest),
        },
        "layout": dict(layout_counts),
        "archives": {
            "2_SCE.BIN": sce_manifest,
            "BMESS2.BIN": {
                "source_size": len(source_bmess),
                "output_size": len(rebuilt_bmess),
                "source_sha256": sha256_bytes(source_bmess),
                "output_sha256": sha256_bytes(rebuilt_bmess),
                "block_count": len(parsed_bmess.blocks),
                "max_cpe_size": max(block.file_end - block.file_start for block in parsed_bmess.blocks),
                "runtime_slot_limit": BMESS_RUNTIME_SLOT_BYTES,
            },
            "2_DEAD.BIN": {
                "source_size": len(source_dead),
                "output_size": len(rebuilt_dead),
                "source_sha256": sha256_bytes(source_dead),
                "output_sha256": sha256_bytes(rebuilt_dead),
                "live_record_count": len(parsed_dead.records),
                "trailing_bytes_preserved": parsed_dead.trailing == parse_dead(source_dead).trailing,
            },
        },
    }

    if not args.skip_track:
        output_track = args.output_dir / (
            "Super Robot Taisen Complete Box Second Korean "
            "v0.1.0-pre (Track 1).bin"
        )
        relocation = relocate_files(
            args.safe_track,
            output_track,
            [
                ("BMESS2.BIN;1", SOURCE_BMESS, out_bmess),
                ("2_SCE.BIN;1", SOURCE_SCE, out_sce),
                ("2_DEAD.BIN;1", SOURCE_DEAD, out_dead),
            ],
        )
        relocation["dynamic_font_executables"] = patch_dynamic_font_track(
            output_track, dynamic_extracted
        )
        relocation["track_sha256"] = sha256_file(output_track)
        relocation["verification"] = verify_final_track(
            output_track,
            {
                "BMESS2.BIN;1": rebuilt_bmess,
                "2_SCE.BIN;1": rebuilt_sce,
                "2_DEAD.BIN;1": rebuilt_dead,
            },
            dynamic_extracted,
        )
        manifest["track"] = relocation

        if not args.skip_xdelta:
            patch_path = args.output_dir / "srwcb-second-korean-v0.1.0-pre.xdelta"
            manifest["xdelta"] = create_and_verify_xdelta(
                args.original_track, output_track, args.xdelta, patch_path
            )

    manifest_path = args.output_dir / "build_manifest.json"
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "passed": True,
                "output_dir": str(args.output_dir),
                "occurrences": len(rows),
                "unique_translation_keys": len(translations),
                "extra_glyphs": len(extra_characters),
                "archive_sizes": {
                    "2_SCE.BIN": len(rebuilt_sce),
                    "BMESS2.BIN": len(rebuilt_bmess),
                    "2_DEAD.BIN": len(rebuilt_dead),
                },
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
