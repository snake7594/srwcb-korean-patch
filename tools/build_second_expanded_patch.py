#!/usr/bin/env python3
"""Build the independently reviewed, variable-length SECOND Korean patch.

This builder has no import or fallback path to the rejected fixed-slot/Google
translation cache.  It rebuilds all three runtime message containers, moves
the enlarged files to fresh ISO extents, patches any additional required font
glyphs, and can produce and decode-verify the final xdelta.
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
for _sub in ("tools", "third-ui", "ex-ui", "tr-ui", "audit", "menu-align", "second-fixes"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------

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
    analyze_bmess_runtime_scratch,
    parse_bmess,
    parse_dead,
    parse_message_record,
    rebuild_bmess_repack,
    rebuild_dead,
    u32,
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
from patch_second_exe_ui import (  # noqa: E402
    collect_korean_ui_texts,
    encode_ui_text,
    patch_second_executable_ui,
    patch_shared_executable_ui,
)
from second_translation_codec import CONTROL_ARGUMENT_BYTES as CONTROL_ARG  # noqa: E402
from second_translation_codec import (  # noqa: E402
    EXTRA_GLYPH_START,
    MAX_LINE_ADVANCE,
    MAX_PAGE_LINES,
    STRUCTURAL_GLYPH_INDICES,
    add_extra_glyph_mapping,
    assemble_translated_record,
    record_geometry,
    load_safe_glyph_map,
    normalise_for_font,
    required_extra_characters,
)


ROOT = _P.WORK
EXTRACTED = _P.EXTRACTED
SAFE_BUILD = _P.BUILD / "exe_font_safe_test"
SAFE_EXTRACTED = SAFE_BUILD / "extracted"
SAFE_FONT = SAFE_BUILD / "font" / "srwcb_font_hangul_test_2816_16x16.bin"
SAFE_TRACK = SAFE_BUILD / "Super Robot Taisen Complete Box Hangul Safe Font Test (Track 1).bin"
ORIGINAL_TRACK = ROOT / "Super Robot Taisen Complete Box (Track 1).bin"
BDF = _P.GALMURI_BDF
LEDGER = _P.LEDGER / "second_translation_ledger.json"
OVERLAY = _P.TRANSLATION / "second_translation_overlay.json"
UI_INVENTORY = (_P.WORK / "research") / "second_exe_ui_full_inventory.json"
UI_SCRIPT_OVERLAY = _P.TRANSLATION / "second_ui_scripts_overlay.json"
UI_TABLE_OVERLAY = _P.TRANSLATION / "second_ui_tables_overlay.json"
UI_NAME_OVERLAY = _P.TRANSLATION / "second_ui_names_overlay.json"
UI_COMMON_OVERLAY = _P.TRANSLATION / "second_ui_common_master_overlay.json"
UI_PREVIEW_OVERLAY = _P.TRANSLATION / "second_ui_preview_overlay.json"
UI_MAP_LABEL_OVERLAY = _P.TRANSLATION / "second_ui_map_labels_overlay.json"
SCE_CONDITIONS_OVERLAY = _P.TRANSLATION / "second_sce_conditions_overlay.json"
# 대사창 상자 (전 시나리오 참조 대사 실측: 줄 폭 <=32, 쪽당 줄수 <=3)
MAX_SCENE_ADVANCE = 32
from analyze_sce_relocation import parse_scenarios as _relocation_scenarios
MAX_SCENE_LINES = 3
BUILD_LABEL = "v0.8.7-full-menus"
DEFAULT_OUTPUT = _P.BUILD / f"second_korean_{BUILD_LABEL}"
XDELTA = ROOT / "xdelta.exe"

SOURCE_SCE = EXTRACTED / "SECOND" / "2_SCE.BIN"
SOURCE_BMESS = EXTRACTED / "BMESS2.BIN"
SOURCE_DEAD = EXTRACTED / "SECOND" / "2_DEAD.BIN"

SECOND_BATTLE_EXECUTABLE = Path("SECOND/SECOND.WAR")
PSX_EXE_FILE_TO_RAM_BIAS = 0x8000F800
SECOND_NAME_POINTER_OFFSET = 0x10CE0C
SECOND_NAME_TABLE_OFFSET = 0x10CE10
SECOND_NAME_TABLE_BYTES = 0x640
SECOND_NAME_COUNT = 400
SECOND_BATTLE_SCRATCH_CODE_OFFSET = 0xC3020
SECOND_BATTLE_SCRATCH_SOURCE = bytes.fromhex("18 80 03 3C 1C 2C 63 34 00 12 04 00")
SECOND_BATTLE_SCRATCH_PATCH = bytes.fromhex("15 80 03 3C 70 BE 63 34 40 12 04 00")
SECOND_BATTLE_SCRATCH_BASE = 0x8015BE70
SECOND_BATTLE_SCRATCH_STRIDE = 0x200
SECOND_BATTLE_SCRATCH_COUNT = 4
SECOND_BATTLE_METADATA_SLOT_BYTES = 0x80
SECOND_BSS_END_SOURCE = 0x8015BE70
SECOND_BSS_END_PATCH = 0x8015C670
SECOND_BSS_END_WORD_OFFSET = 0x800
SECOND_BSS_CLEAR_END_CODE_OFFSET = 0x44354
SECOND_BSS_CLEAR_END_SOURCE = bytes.fromhex("16 80 03 3C 70 BE 63 24")
SECOND_BSS_CLEAR_END_PATCH = bytes.fromhex("16 80 03 3C 70 C6 63 24")
SECOND_HEAP_BASE_CODE_OFFSET = 0x4439C
SECOND_HEAP_BASE_SOURCE = bytes.fromhex("16 80 04 3C 70 BE 84 24")
SECOND_HEAP_BASE_PATCH = bytes.fromhex("16 80 04 3C 70 C6 84 24")
SECOND_HEAP_START_PATCH = SECOND_BSS_END_PATCH + 4
SECOND_HEAP_START_ADD_CODE_OFFSET = 0x443DC
SECOND_HEAP_START_ADD_SOURCE = bytes.fromhex("04 00 84 20")
SECOND_HEAP_CEILING = 0x801F8000
SECOND_MINIMUM_HEAP_BYTES = 0x60000


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
    pristine_second = (EXTRACTED / SECOND_BATTLE_EXECUTABLE).read_bytes()
    pristine_font_offset = FONT_EXE_LAYOUT[SECOND_BATTLE_EXECUTABLE]
    pristine_font = pristine_second[
        pristine_font_offset:pristine_font_offset + FONT_BYTES
    ]
    if len(pristine_font) != FONT_BYTES:
        raise ValueError("pristine SECOND font has an unexpected size")
    for index in STRUCTURAL_GLYPH_INDICES:
        start = index * GLYPH_BYTES
        patched_font[start:start + GLYPH_BYTES] = pristine_font[
            start:start + GLYPH_BYTES
        ]
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
        "restored_structural_glyphs": sorted(STRUCTURAL_GLYPH_INDICES),
    }, dynamic_extracted


def encode_index(index: int) -> bytes:
    if index < 0xEB:
        return bytes((index,))
    return bytes((0xEB + (index >> 8), index & 0xFF))


def replace_unique_equal_sized_blob(
    data: bytes,
    source_blob: bytes,
    replacement_blob: bytes,
) -> tuple[bytes, int]:
    """Replace one exact embedded table while rejecting ambiguous matches."""

    if not source_blob or len(source_blob) != len(replacement_blob):
        raise ValueError("embedded table replacement must be nonempty and equal-sized")
    offset = data.find(source_blob)
    if offset < 0:
        raise ValueError("embedded source table was not found")
    if data.find(source_blob, offset + 1) >= 0:
        raise ValueError("embedded source table is not unique")
    output = bytearray(data)
    output[offset:offset + len(source_blob)] = replacement_blob
    return bytes(output), offset


def patch_embedded_bmess_tables(
    output_dir: Path,
    executable_root: Path | None,
    source_bmess: bytes,
    rebuilt_bmess: bytes,
) -> tuple[Path, list[dict[str, Any]]]:
    """Patch the BMESS2 outer offset table used by SECOND.WAR only.

    The game does not select CPE blocks from the table stored at the start of
    BMESS2.BIN.  It uses an exact copy linked into the executable instead.  If
    the archive is repacked but this copy remains stale, the first battle
    message load starts in the middle of a CPE block and the battle stalls.

    Other executables contain the same original bytes, but their registry slot
    0x1E aliases BMESS3.BIN or BMESS4.BIN.  Replacing those shared blobs with a
    repacked BMESS2 table could therefore damage the other three games.  Keep
    this SECOND-only build deliberately scoped to SECOND.WAR.
    """

    source_table_size = u32(source_bmess, 0)
    rebuilt_table_size = u32(rebuilt_bmess, 0)
    if source_table_size != rebuilt_table_size:
        raise ValueError("BMESS outer table size changed during rebuild")
    source_table = source_bmess[:source_table_size]
    rebuilt_table = rebuilt_bmess[:rebuilt_table_size]
    if source_table == rebuilt_table:
        raise ValueError("BMESS rebuild unexpectedly left its outer table unchanged")

    if executable_root is None:
        executable_root = output_dir / "runtime_extracted"
        base_root = SAFE_EXTRACTED
    else:
        base_root = executable_root

    # The final raw-track patcher consumes all five runtime executables.  When
    # this helper owns the output tree, seed unchanged copies before applying
    # the one SECOND-specific table change.
    if base_root != executable_root:
        for relative_path in TRACK_EXE_LAYOUT:
            source_path = base_root / relative_path
            destination = executable_root / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, destination)

    manifest: list[dict[str, Any]] = []
    relative_path = SECOND_BATTLE_EXECUTABLE
    source_path = base_root / relative_path
    patched, offset = replace_unique_equal_sized_blob(
        source_path.read_bytes(), source_table, rebuilt_table
    )
    destination = executable_root / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(patched)
    if patched[offset:offset + source_table_size] != rebuilt_table:
        raise AssertionError(f"{relative_path}: embedded BMESS table patch failed")
    manifest.append(
        {
            "path": str(relative_path).replace("\\", "/"),
            "file_offset": offset,
            "table_size": source_table_size,
            "scope": "SECOND.WAR only; other executable aliases preserved",
            "source_table_sha256": sha256_bytes(source_table),
            "rebuilt_table_sha256": sha256_bytes(rebuilt_table),
            "executable_sha256": sha256_bytes(patched),
        }
    )
    return executable_root, manifest


def load_second_battle_speaker_prefix_lengths(executable: bytes) -> tuple[int, ...]:
    """Read the 400 FF-terminated speaker names used by the battle evaluator."""

    expected_pointer = PSX_EXE_FILE_TO_RAM_BIAS + SECOND_NAME_TABLE_OFFSET
    if u32(executable, SECOND_NAME_POINTER_OFFSET) != expected_pointer:
        raise ValueError("SECOND battle speaker-name table pointer changed")
    if len(executable) < SECOND_NAME_TABLE_OFFSET + SECOND_NAME_TABLE_BYTES:
        raise ValueError("SECOND battle speaker-name table has an unexpected shape")

    lengths: list[int] = []
    for index in range(SECOND_NAME_COUNT):
        field = SECOND_NAME_TABLE_OFFSET + index * 4
        target = field + struct.unpack_from("<i", executable, field)[0]
        if target < 0x800 or target >= len(executable):
            raise ValueError(
                f"SECOND battle speaker {index} has invalid target {target:#x}"
            )
        record = parse_message_record(executable, target)
        lengths.append(record.end - record.start - 1)  # runtime omits the name FF
    if len(lengths) != SECOND_NAME_COUNT:
        raise AssertionError("SECOND speaker-name table count changed")
    return tuple(lengths)


def mips_lui_addiu_pair(register: int, address: int) -> bytes:
    """Encode ``lui reg,hi; addiu reg,reg,lo`` with signed-low carry."""

    if not 0 <= register < 32 or not 0 <= address <= 0xFFFFFFFF:
        raise ValueError("invalid MIPS register/address")
    high = ((address + 0x8000) >> 16) & 0xFFFF
    low = address & 0xFFFF
    lui = 0x3C000000 | (register << 16) | high
    addiu = 0x24000000 | (register << 21) | (register << 16) | low
    return struct.pack("<II", lui, addiu)


def patch_second_battle_scratch(
    executable_root: Path,
    rebuilt_bmess: bytes,
) -> dict[str, Any]:
    """Reserve four independent 0x200-byte slots below SECOND's heap.

    The retail evaluator uses 0x100 bytes per mode and has no bounds check.
    The first translated BMESS path needs 358 bytes, so it would overwrite the
    following mode.  Clear and reserve 0x8015BE70..0x8015C66F.  The menu
    patcher keeps the retail loaded-image boundary, so the BIOS heap starts
    immediately after this guarded scratch reservation.
    """

    executable_path = executable_root / SECOND_BATTLE_EXECUTABLE
    executable = bytearray(executable_path.read_bytes())
    code_end = SECOND_BATTLE_SCRATCH_CODE_OFFSET + len(SECOND_BATTLE_SCRATCH_SOURCE)
    if executable[SECOND_BATTLE_SCRATCH_CODE_OFFSET:code_end] != SECOND_BATTLE_SCRATCH_SOURCE:
        raise ValueError("SECOND battle scratch setup instructions changed")
    if u32(executable, SECOND_BSS_END_WORD_OFFSET) != SECOND_BSS_END_SOURCE:
        raise ValueError("SECOND module BSS-end word changed")
    clear_end = SECOND_BSS_CLEAR_END_CODE_OFFSET + len(SECOND_BSS_CLEAR_END_SOURCE)
    if executable[SECOND_BSS_CLEAR_END_CODE_OFFSET:clear_end] != SECOND_BSS_CLEAR_END_SOURCE:
        raise ValueError("SECOND BSS clear-end instruction changed")
    heap_end = SECOND_HEAP_BASE_CODE_OFFSET + len(SECOND_HEAP_BASE_SOURCE)
    if executable[SECOND_HEAP_BASE_CODE_OFFSET:heap_end] != SECOND_HEAP_BASE_SOURCE:
        raise ValueError("SECOND InitHeap base instruction changed")
    if (
        executable[
            SECOND_HEAP_START_ADD_CODE_OFFSET:
            SECOND_HEAP_START_ADD_CODE_OFFSET + len(SECOND_HEAP_START_ADD_SOURCE)
        ]
        != SECOND_HEAP_START_ADD_SOURCE
    ):
        raise ValueError("SECOND InitHeap +4 delay-slot instruction changed")

    reservation_bytes = SECOND_BATTLE_SCRATCH_STRIDE * SECOND_BATTLE_SCRATCH_COUNT
    if SECOND_BATTLE_SCRATCH_BASE != SECOND_BSS_END_SOURCE:
        raise AssertionError("battle scratch must begin at the retail BSS/heap boundary")
    if SECOND_BATTLE_SCRATCH_BASE + reservation_bytes != SECOND_BSS_END_PATCH:
        raise AssertionError("relocated BSS/heap boundary does not reserve the scratch exactly")

    prefix_lengths = load_second_battle_speaker_prefix_lengths(bytes(executable))
    analysis = analyze_bmess_runtime_scratch(rebuilt_bmess, prefix_lengths)
    archive = parse_bmess(rebuilt_bmess)
    maximum_cpe_size = max(block.file_end - block.file_start for block in archive.blocks)
    if analysis["maximum_bytes"] > SECOND_BATTLE_SCRATCH_STRIDE:
        raise ValueError(
            f"battle scratch needs {analysis['maximum_bytes']:#x}; relocated slot is "
            f"only {SECOND_BATTLE_SCRATCH_STRIDE:#x} bytes"
        )
    metadata_bytes = analysis["maximum_leaf_count"] * 10 + 2
    if metadata_bytes > SECOND_BATTLE_METADATA_SLOT_BYTES:
        raise ValueError(
            f"battle metadata needs {metadata_bytes:#x}; mode slot is only "
            f"{SECOND_BATTLE_METADATA_SLOT_BYTES:#x} bytes"
        )

    if executable[:8] != b"PS-X EXE":
        raise ValueError("SECOND battle executable lost its PS-X EXE header")
    text_address = u32(executable, 0x18)
    text_size = u32(executable, 0x1C)
    if len(executable) != text_size + 0x800:
        raise ValueError("SECOND PS-X EXE t_size/file size mismatch")
    loaded_end = text_address + text_size
    if len(executable) != 0x12B000 or loaded_end != 0x8013A800:
        raise ValueError("SECOND UI repack changed the retail loaded-image boundary")
    module_end = SECOND_BSS_END_PATCH
    heap_pair_target = SECOND_BSS_END_PATCH
    heap_start = heap_pair_target + 4
    heap_bytes = SECOND_HEAP_CEILING - heap_start
    if heap_bytes < SECOND_MINIMUM_HEAP_BYTES:
        raise ValueError(
            f"SECOND UI/scratch reservation leaves only {heap_bytes:#x} heap bytes"
        )
    heap_patch = mips_lui_addiu_pair(4, heap_pair_target)

    executable[SECOND_BSS_END_WORD_OFFSET:SECOND_BSS_END_WORD_OFFSET + 4] = struct.pack(
        "<I", module_end
    )
    executable[SECOND_BSS_CLEAR_END_CODE_OFFSET:clear_end] = SECOND_BSS_CLEAR_END_PATCH
    executable[SECOND_HEAP_BASE_CODE_OFFSET:heap_end] = heap_patch
    executable[SECOND_BATTLE_SCRATCH_CODE_OFFSET:code_end] = SECOND_BATTLE_SCRATCH_PATCH
    if executable[SECOND_BATTLE_SCRATCH_CODE_OFFSET:code_end] != SECOND_BATTLE_SCRATCH_PATCH:
        raise AssertionError("SECOND battle scratch instruction patch failed")
    if u32(executable, SECOND_BSS_END_WORD_OFFSET) != module_end:
        raise AssertionError("SECOND module BSS-end word patch failed")
    if executable[SECOND_BSS_CLEAR_END_CODE_OFFSET:clear_end] != SECOND_BSS_CLEAR_END_PATCH:
        raise AssertionError("SECOND BSS clear-end patch failed")
    if executable[SECOND_HEAP_BASE_CODE_OFFSET:heap_end] != heap_patch:
        raise AssertionError("SECOND InitHeap base patch failed")
    executable_path.write_bytes(executable)

    return {
        "path": str(SECOND_BATTLE_EXECUTABLE).replace("\\", "/"),
        "file_offset": SECOND_BATTLE_SCRATCH_CODE_OFFSET,
        "ram_address": PSX_EXE_FILE_TO_RAM_BIAS + SECOND_BATTLE_SCRATCH_CODE_OFFSET,
        "source_bytes": SECOND_BATTLE_SCRATCH_SOURCE.hex(" ").upper(),
        "patched_bytes": SECOND_BATTLE_SCRATCH_PATCH.hex(" ").upper(),
        "scratch_base_ram": SECOND_BATTLE_SCRATCH_BASE,
        "scratch_stride": SECOND_BATTLE_SCRATCH_STRIDE,
        "scratch_slot_count": SECOND_BATTLE_SCRATCH_COUNT,
        "scratch_end_ram": (
            SECOND_BATTLE_SCRATCH_BASE
            + reservation_bytes
        ),
        "storage_kind": "BSS extension excluded from BIOS heap",
        "reservation_bytes": reservation_bytes,
        "retail_bss_end_ram": SECOND_BSS_END_SOURCE,
        "scratch_clear_end_ram": SECOND_BSS_END_PATCH,
        "module_end_ram": module_end,
        "loaded_image_end_ram": loaded_end,
        "patched_heap_pair_target_ram": heap_pair_target,
        "patched_heap_start_ram": heap_start,
        "heap_guard_bytes": 4,
        "heap_bytes": heap_bytes,
        "boundary_patches": [
            {
                "kind": "module_bss_end_word",
                "file_offset": SECOND_BSS_END_WORD_OFFSET,
                "source": struct.pack("<I", SECOND_BSS_END_SOURCE).hex(" ").upper(),
                "patched": struct.pack("<I", module_end).hex(" ").upper(),
            },
            {
                "kind": "startup_bss_clear_end",
                "file_offset": SECOND_BSS_CLEAR_END_CODE_OFFSET,
                "source": SECOND_BSS_CLEAR_END_SOURCE.hex(" ").upper(),
                "patched": SECOND_BSS_CLEAR_END_PATCH.hex(" ").upper(),
            },
            {
                "kind": "startup_initheap_base",
                "file_offset": SECOND_HEAP_BASE_CODE_OFFSET,
                "source": SECOND_HEAP_BASE_SOURCE.hex(" ").upper(),
                "patched": heap_patch.hex(" ").upper(),
            },
        ],
        "metadata_slot_bytes": SECOND_BATTLE_METADATA_SLOT_BYTES,
        "maximum_metadata_bytes": metadata_bytes,
        "cpe_size_limit": BMESS_RUNTIME_SLOT_BYTES,
        "maximum_cpe_size": maximum_cpe_size,
        "speaker_name_count": len(prefix_lengths),
        "maximum_speaker_prefix_bytes": max(prefix_lengths),
        "analysis": analysis,
        "executable_sha256": sha256_bytes(bytes(executable)),
    }


def _box_overrides() -> dict[str, str]:
    """대사창에 도저히 안 들어가 문장을 다시 쓴 대사 (레코드 id -> 한국어)."""
    path = _P.TRANSLATION / "dialogue_box_overrides.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("records", {})


_EXACT_CUTS: list = []


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

    # ---- 대사 상자 크기 ----------------------------------------------------
    # 상자는 장면마다 크기가 다르다(시나리오 대사 폭 16~32·페이지당 1~3줄,
    # 전투 대사 폭 ~29·2줄). 고정 18×3 으로 짜 넣으면 줄이 넘쳐 다음 대사가
    # 밀리거나 문장 중간부터 그려진다. 그래서 **레트일 원문이 실제로 쓴 크기**를
    # 그대로 따라간다: 폭은 같은 시나리오 안에서 가장 넓었던 줄, 페이지당 줄수는
    # 그 레코드가 쓰던 줄수. 일본어가 들어갔던 크기라면 한국어도 확실히 들어간다.
    geo = _dialogue_geometry(source_sce, source_bmess, source_dead, rows, sources)
    overrides = _box_overrides()

    for row in rows:
        kind = row["kind"]
        source = sources[kind]
        old_raw = verify_source_row(row, source)
        key = row["translation_memory_key"]
        ko_parts = translations[key]["ko_parts"]
        # 대사창에 도저히 안 들어가 문장을 다시 쓴 대사 (뜻은 그대로, 표현만 짧게)
        ovr = overrides.get(row["id"])
        if isinstance(ovr, dict):                 # 쪽이 나뉜 대사는 파트별로
            ko_parts = {k: ovr.get(k, v) for k, v in ko_parts.items()}
        elif isinstance(ovr, str) and len(ko_parts) == 1:
            only = next(iter(ko_parts))
            ko_parts = {only: ovr}
        _g = geo(kind, row["source"]["offset"], old_raw)
        encoded, layout = assemble_translated_record(
            row["japanese"]["translation_parts"], ko_parts, glyph_map,
            geometry=_g[:3], max_bytes=_g[3],
        )
        if kind == "scenario" and _os.environ.get("SRWCB_EXACT_LEN", "") not in ("", "0"):
            if len(encoded) > len(old_raw):
                _EXACT_CUTS.append((row["id"], row["source"]["offset"],
                                    len(old_raw), len(encoded) - len(old_raw),
                                    " / ".join(ko_parts.values())))
            # 진단용: 레코드를 원문과 **정확히 같은 바이트 길이**로 (자르거나 공백 채움)
            want = len(old_raw)
            enc = bytearray(encoded)
            if len(enc) > want:
                keep = 0; q = 0
                while q < len(enc) - 1:
                    b = enc[q]
                    if b == 0xFF:
                        break
                    ln = 1 if b < 0xEB else (2 if b <= 0xF5 else 1 + CONTROL_ARG.get(b, 0))
                    if q + ln > want - 1:
                        break
                    q += ln; keep = q
                enc = bytearray(enc[:keep]) + bytes([0xFF])
            if len(enc) < want:
                enc = bytearray(enc[:-1]) + bytes(want - len(enc)) + bytes([0xFF])
            encoded = bytes(enc)
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


def patch_runtime_executables_track(
    track_path: Path,
    runtime_extracted: Path,
    *,
    relocated: frozenset[Path] = frozenset(),
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    with track_path.open("r+b") as track:
        for relative_path, lba in TRACK_EXE_LAYOUT.items():
            if relative_path in relocated:
                continue
            source = (SAFE_EXTRACTED / relative_path).read_bytes()
            patched = (runtime_extracted / relative_path).read_bytes()
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
    runtime_extracted: Path,
    *,
    relocated_executables: frozenset[Path] = frozenset(),
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
    with track_path.open("rb") as track:
        for relative_path, lba in TRACK_EXE_LAYOUT.items():
            if relative_path in relocated_executables:
                continue
            expected = (runtime_extracted / relative_path).read_bytes()
            actual = bytearray()
            sector_count = math.ceil(len(expected) / USER_DATA_SIZE)
            for index in range(sector_count):
                track.seek((lba + index) * SECTOR_SIZE)
                sector = track.read(SECTOR_SIZE)
                if len(sector) != SECTOR_SIZE:
                    raise AssertionError(f"{relative_path}: executable runs past physical track")
                verify_sector(sector)
                actual.extend(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
            if bytes(actual[:len(expected)]) != expected:
                raise AssertionError(f"{relative_path}: final executable/font mismatch")
            executable_checks.append(
                {
                    "path": str(relative_path).replace("\\", "/"),
                    "lba": lba,
                    "sha256": sha256_bytes(expected),
                    "sector_count": sector_count,
                    "edc_ecc_verified": True,
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
        [
            str(xdelta_path),
            "-A",
            "-9",
            "-e",
            "-s",
            str(original_track),
            str(patched_track),
            str(output_path),
        ],
        check=True,
    )
    vcdiff_header = output_path.read_bytes()[:5]
    if len(vcdiff_header) != 5 or vcdiff_header[:4] != b"\xD6\xC3\xC4\x00":
        raise AssertionError("xdelta output has an invalid VCDIFF header")
    if vcdiff_header[4] & 0x04:
        raise AssertionError("xdelta output contains a path-leaking VCDIFF appheader")
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
        "appheader_present": False,
        "decoded_sha256": decoded_hash,
        "decoded_matches_patched_track": True,
    }



# 시나리오 대사 상자: 레트일이 실제로 채운 최대 폭(전 시나리오 실측 32)
SCENARIO_BOX_ADVANCE = 32
NARROW_WINDOW_ADVANCE = 24


def _has_page_break(raw: bytes) -> bool:
    """원문 레코드가 F7(쪽 나눔)을 쓰는가. 제어 토큰으로 정확히 훑는다."""
    from second_translation_codec import CONTROL_ARGUMENT_BYTES
    p = 0
    while p < len(raw):
        b = raw[p]
        if b == 0xFF:
            return False
        if b < 0xEB:
            p += 1
        elif b <= 0xF5:
            p += 2
        else:
            if b == 0xF7:
                return True
            p += 1 + CONTROL_ARGUMENT_BYTES.get(b, 0)
    return False


def _retail_line_advances(raw: bytes) -> list[int]:
    """원문 레코드의 줄별 advance (F6/F7 로 나눔)."""
    from second_translation_codec import glyph_advance, CONTROL_ARGUMENT_BYTES
    out, adv, phase, p = [], 0, 0, 0
    while p < len(raw):
        b = raw[p]
        if b == 0xFF:
            break
        if b < 0xEB:
            idx = b; p += 1
        elif b <= 0xF5:
            idx = (b - 0xEB) << 8 | raw[p + 1]; p += 2
        else:
            if b in (0xF6, 0xF7):
                out.append(adv); adv = 0; phase = 0
            p += 1 + CONTROL_ARGUMENT_BYTES.get(b, 0)
            continue
        step, phase = glyph_advance(idx, phase)
        adv += step
    out.append(adv)
    return out


def _dialogue_geometry(source_sce, source_bmess, source_dead, rows, sources):
    """(kind, offset, retail_raw) -> (줄 폭, 페이지당 줄수)."""
    # 전투·사망: 상자가 하나뿐이니 아카이브 전체 최대치를 쓴다
    def archive_max(kind):
        w = h = 1
        for row in rows:
            if row["kind"] != kind:
                continue
            raw = verify_source_row(row, sources[kind])
            a, b = record_geometry(bytes(raw))
            w, h = max(w, a), max(h, b)
        return w, h

    battle = archive_max("battle_message")
    death = archive_max("death_quote")

    # 시나리오 앞머리의 작전목적(승리/패배조건) 블록은 **원문이 쓴 줄 수 그대로**
    # 여야 한다. 줄을 하나라도 더 넣으면 작전목적 창이 깨지고 게임이 멈춘다
    # (2026-08-09 제보, 제3차 8화). 대사는 상자(3줄)를 다 써도 된다.
    from analyze_sce_relocation import objective_block_records as _OBJ
    _strict_lines = _OBJ(bytes(source_sce))
    # 작전목적 블록을 레트일 배치로 못박는 건 **제3차만** 필요하다. 제2차·EX 는
    # 이 블록이 원문보다 길어져도 작전목적 화면이 멀쩡하다(실사용 확인). 굳이
    # 못박으면 조건문만 잘려 손해다.
    _PIN_OBJECTIVE = _os.environ.get("SRWCB_PIN_OBJECTIVE") == "1"

    # 대사 레코드의 **바이트** 상한. 엔진은 한 쪽(최대 3줄)을 실행파일 안 버퍼로
    # 복사하는데 그 버퍼가 원문 기준으로 잡혀 있다. 원문이 만든 적 없는 길이를
    # 넣으면 앞부분이 밀려 나가 화자가 사라지고 문장 중간부터 보인다.
    sce_byte_cap = 1
    for row in rows:
        if row["kind"] != "scenario":
            continue
        raw = verify_source_row(row, sources["scenario"])
        sce_byte_cap = max(sce_byte_cap, len(bytes(raw)))

    def get(kind, offset, raw):
        if kind == "scenario":
            # 대사창은 **폭 32칸 · 한 쪽 3줄**이다. 세 게임 전 시나리오의 참조 대사
            # 레코드를 다 훑어 확인했다 — 줄을 바꾼 지점은 16~32 에 몰려 있고 32 를
            # 넘는 줄이 하나도 없다. 쪽당 줄수도 3 이 상한이다.
            #
            # 시나리오 안에서 상자를 유추하려 들면 안 된다. 같은 시나리오에 폭 276
            # 짜리 특수 레코드가 섞여 있어서, 그걸 상자로 잡으면 대사가 한 줄로
            # 늘어지고 렌더러가 스스로 줄을 접다가 2바이트 글자의 앞바이트를 잃는다
            # (화면이 낱말 중간부터 시작하고 첫 글자가 깨지는 그 증상).
            #
            # 원문이 32x3 을 넘게 쓴 드문 레코드는 그 레코드가 쓴 만큼을 상자로 본다.
            #
            # ★ 쪽 나눔(F7)은 **원문이 그 레코드에서 쓴 경우에만** 쓴다.
            #   대사창(참조 대사) 레코드에는 원문이 F7 을 한 개도 안 쓴다
            #   (제3차 2,540개 중 0개, EX 3,653개 중 0개). 대사창 경로는 F7 을
            #   처리하지 못해서, 넣으면 대사가 꼬이다가 멈춘다(제3차 8화).
            #   이벤트 스크립트가 부르는 레코드에는 원문도 F7 을 쓴다 — 거기만 허용.
            # ★ 줄 수는 **상자 높이(3줄)** 까지 쓸 수 있다.
            #   한때 '원문 레코드가 쓴 줄 수 그대로' 로 묶어 뒀는데, 그건 제3차
            #   대사 밀림(포인터 `B6 00` 미재조준)을 줄 수 탓으로 오해한 결과였다.
            #   포매터(0x800CB808)는 3번째 F6/F7 에서 멈추고 소비한 바이트만큼
            #   포인터를 옮겨 준다 — 즉 우리 데이터를 실제로 훑는다. 원문이 1줄인
            #   레코드를 1줄로 묶으면 한국어가 안 들어가 축약이 5단계까지 올라가서
            #   띄어쓰기가 사라지고 화자 이름까지 지워진다(2026-08-09 제보).
            width, lines = record_geometry(bytes(raw))
            # 바이트 상한은 **걸지 않는다.** 한때 원문 길이로 묶었지만, 그건
            # 대사 포인터 `B6 00 <변위16>` 을 재조준하지 못하던 걸 우회하려던
            # 미봉책이었고 (2026-08-09 수정), 그 대가로 축약 단계가 과하게 올라가
            # 띄어쓰기가 사라지고 화자 이름까지 지워졌다. 이제 레코드는 자유롭게
            # 커질 수 있으니 **상자에만 맞추면 된다.**
            # 작전목적 블록은 레트일 배치를 그대로 지켜야 하므로 **바이트 예산**도
            # 건다. 그래야 축약 단계가 먼저 돌고, 뒤에서 pin_objective_block 이
            # 자를 일이 거의 없다. 대사는 예산 없이 자유롭게 커진다.
            strict = offset in _strict_lines and _PIN_OBJECTIVE
            box_lines = lines if strict else max(lines, MAX_PAGE_LINES)
            return (max(width, MAX_SCENE_ADVANCE), box_lines,
                    _has_page_break(bytes(raw)),
                    len(bytes(raw)) if strict else None)
        w, h = battle if kind == "battle_message" else death
        return w, h, True, None

    return get

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", type=Path, default=LEDGER)
    ap.add_argument("--overlay", type=Path, default=OVERLAY)
    ap.add_argument("--ui-inventory", type=Path, default=UI_INVENTORY)
    ap.add_argument("--ui-script-overlay", type=Path, default=UI_SCRIPT_OVERLAY)
    ap.add_argument("--ui-table-overlay", type=Path, default=UI_TABLE_OVERLAY)
    ap.add_argument("--ui-name-overlay", type=Path, default=UI_NAME_OVERLAY)
    ap.add_argument("--ui-common-overlay", type=Path, default=UI_COMMON_OVERLAY)
    ap.add_argument("--ui-preview-overlay", type=Path, default=UI_PREVIEW_OVERLAY)
    ap.add_argument("--ui-map-label-overlay", type=Path, default=UI_MAP_LABEL_OVERLAY)
    ap.add_argument("--sce-conditions-overlay", type=Path, default=SCE_CONDITIONS_OVERLAY)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--safe-track", type=Path, default=SAFE_TRACK)
    ap.add_argument("--original-track", type=Path, default=ORIGINAL_TRACK)
    ap.add_argument("--xdelta", type=Path, default=XDELTA)
    ap.add_argument("--skip-track", action="store_true", help="build and verify archives only")
    ap.add_argument("--skip-xdelta", action="store_true")
    args = ap.parse_args()

    rows, translations, ledger_doc = validate_translation_inputs(args.ledger, args.overlay)
    ui_overlay_paths = [
        args.ui_script_overlay,
        args.ui_table_overlay,
        args.ui_name_overlay,
        args.ui_common_overlay,
        args.ui_preview_overlay,
    ]
    normalised_texts = [
        normalise_for_font(value)[0]
        for translation in translations.values()
        for value in translation["ko_parts"].values()
    ]
    normalised_texts.extend(
        normalise_for_font(value)[0]
        for value in collect_korean_ui_texts(ui_overlay_paths)
    )
    # Map-label heap and SCE condition overlays carry their Korean outside the
    # standard UI overlay schemas; feed their text into the dynamic font too.
    map_label_doc = json.loads(args.ui_map_label_overlay.read_text(encoding="utf-8"))
    sce_conditions_doc = json.loads(
        args.sce_conditions_overlay.read_text(encoding="utf-8")
    )
    normalised_texts.extend(
        normalise_for_font(str(row.get("korean_text", "")).replace("[F6]", "").replace("[F7]", ""))[0]
        for doc in (map_label_doc, sce_conditions_doc)
        for row in doc.get("records", [])
    )
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

    # Merge the non-dialogue SCE strings (victory/defeat conditions, sirens,
    # unquoted lines) into the same variable-length record replacement set.
    # Records may carry a leading VM/code prefix that is preserved verbatim;
    # only the Japanese text span from text_start_rel onward is re-encoded.
    sce_condition_manifest: list[dict[str, Any]] = []
    for row in sce_conditions_doc.get("records", []):
        offset = int(row["offset"])
        length = int(row["record_bytes"])
        raw = source_sce[offset:offset + length]
        if hashlib.sha256(raw).hexdigest() != row["raw_sha256"]:
            raise ValueError(f"SCE condition source changed at {offset:#x}")
        if offset in sce_repl:
            raise ValueError(
                f"SCE condition {offset:#x} collides with a ledger replacement"
            )
        prefix = raw[: int(row["text_start_rel"])]
        korean = str(row["korean_text"])
        encoded_parts = [
            encode_ui_text(part.replace("[F6]", "\n"), glyph_map, terminate=False)
            for part in korean.split("[F7]")
        ]
        encoded = b"\xF7".join(encoded_parts)
        sce_repl[offset] = prefix + encoded + b"\xFF"
        sce_condition_manifest.append(
            {
                "offset": offset,
                "source_bytes": length,
                "replacement_bytes": len(sce_repl[offset]),
                "korean_text": korean,
            }
        )

    import fix_sce_event_refs as _FX
    sce_repl, _ = _FX.harden_against_ff_operands(
        source_sce, sce_repl, rebuild_second_sce)
    rebuilt_sce, sce_manifest = rebuild_second_sce(source_sce, sce_repl)
    rebuilt_bmess = rebuild_bmess_repack(source_bmess, bm_repl)
    rebuilt_dead = rebuild_dead(source_dead, dead_repl)
    parsed_bmess = parse_bmess(rebuilt_bmess)
    parsed_dead = parse_dead(rebuilt_dead)
    runtime_extracted, embedded_bmess_tables = patch_embedded_bmess_tables(
        args.output_dir,
        dynamic_extracted,
        source_bmess,
        rebuilt_bmess,
    )
    executable_ui = patch_second_executable_ui(
        runtime_extracted,
        glyph_map,
        args.ui_inventory,
        ui_overlay_paths,
        label_overlay_path=args.ui_map_label_overlay,
    )
    shared_executable_ui = patch_shared_executable_ui(
        runtime_extracted / Path("SLPS_020.70"),
        args.ui_script_overlay,
        glyph_map,
    )
    battle_scratch = patch_second_battle_scratch(runtime_extracted, rebuilt_bmess)
    for row in embedded_bmess_tables:
        executable_path = runtime_extracted / Path(row["path"])
        row["executable_sha256"] = sha256_file(executable_path)

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
        "font": font_manifest,
        "executable_ui": executable_ui,
        "shared_executable_ui": shared_executable_ui,
        "embedded_bmess_tables": embedded_bmess_tables,
        "battle_scratch": battle_scratch,
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
        output_track = args.output_dir / f"Super Robot Taisen Complete Box Second Korean {BUILD_LABEL} (Track 1).bin"
        relocation = relocate_files(
            args.safe_track,
            output_track,
            [
                ("BMESS2.BIN;1", SOURCE_BMESS, out_bmess),
                ("2_SCE.BIN;1", SOURCE_SCE, out_sce),
                ("2_DEAD.BIN;1", SOURCE_DEAD, out_dead),
                (
                    "SECOND.WAR;1",
                    SAFE_EXTRACTED / SECOND_BATTLE_EXECUTABLE,
                    runtime_extracted / SECOND_BATTLE_EXECUTABLE,
                ),
            ],
        )
        relocation["runtime_executables"] = patch_runtime_executables_track(
            output_track,
            runtime_extracted,
            relocated=frozenset({SECOND_BATTLE_EXECUTABLE}),
        )
        relocation["track_sha256"] = sha256_file(output_track)
        relocation["verification"] = verify_final_track(
            output_track,
            {
                "BMESS2.BIN;1": rebuilt_bmess,
                "2_SCE.BIN;1": rebuilt_sce,
                "2_DEAD.BIN;1": rebuilt_dead,
                "SECOND.WAR;1": (
                    runtime_extracted / SECOND_BATTLE_EXECUTABLE
                ).read_bytes(),
            },
            runtime_extracted,
            relocated_executables=frozenset({SECOND_BATTLE_EXECUTABLE}),
        )
        manifest["track"] = relocation

        if not args.skip_xdelta:
            patch_path = args.output_dir / f"srwcb-second-korean-{BUILD_LABEL}.xdelta"
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
