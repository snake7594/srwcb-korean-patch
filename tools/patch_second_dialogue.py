#!/usr/bin/env python3
"""Build a small, length-changing Korean dialogue test for SECOND/2_SCE.BIN.

The renderer stores dialogue as FF-terminated records.  Glyphs below EB are
single-byte indices; higher glyphs (including the injected Hangul block) use
the EB-prefixed two-byte form.  F6 is the explicit line advance opcode.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import struct
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))
from patch_raw_track_exes import (  # noqa: E402
    ECC_P_OFFSET,
    ECC_Q_OFFSET,
    EDC_OFFSET,
    SECTOR_SIZE,
    USER_DATA_OFFSET,
    USER_DATA_SIZE,
    rebuild_mode2_form1,
)

MAX_LINES = 3
MAX_LINE_GLYPHS = 26
FONT_MAP = Path("korean_patch/test_build/exe_font_test/font/hangul_ksx1001_exe_mapping.tsv")
REVIEWED_MAP = Path("korean_patch/research/srwcb_embedded_font_mapping_reviewed.json")
SOURCE_SCE = Path("korean_patch/extracted/SECOND/2_SCE.BIN")

# These records are the first visible exchanges in the opening scenario.  The
# first replacement deliberately uses all three display lines to exercise the
# renderer limit, while keeping every line inside the safe 26-cell test width.
TRANSLATIONS: dict[int, str] = {
    0x1036: "장교「말이 너무 길어서\n아래 내용은 생략하도록\n하겠습니다. 푸풋」",
    0x1056: "성공!⟦F7:63 4D⟧가 보자!!",
    0x1067: "아스카「바보 신지!!」",
    0x1074: "아스카「바보 신지!」",
    0x1080: "아스카「바보 신지」",
    0x108B: "아스카「바보 신」",
}

# The six records visible in the screenshots supplied for the second game.
# These are the opening conversation in scenario 1 (the second SCE pool), not
# the earlier synthetic records above.
CAPTURE_TRANSLATIONS: dict[int, str] = {
    0x3A1B: "브라이트「DC와 싸우려면, 전력 정비가 먼저다.\n우선 벨파스트 기지에 갇힌 카미유와\nZ건담을 구출한다. 질문은 있나?」",
    0x3A70: "료「적의 전력은 어느 정도입니까?」",
    0x3A87: "브라이트「정보에 따르면, 별일 아니다.\n지금 우리 전력으로 제압할 수 있을 거다」",
    0x3ABD: "코우지「헤헤, 적의 수가 조금 많아도,\n내가 척척 정리해 주지!」",
    0x3AF2: "사야카「정말, 말만 잘한다니까...」",
    0x3B0D: "아무로「아무튼 서둘러야 합니다. 벨파스트로\n향합시다」",
}

# A fixed-slot variant for reliable in-game sequencing.  Every encoded record
# is shorter than its Japanese slot and is padded with blank glyphs before FF,
# so no later dialogue address moves.
CAPTURE_SAFE_TRANSLATIONS: dict[int, str] = {
    0x3A1B: "브라이트「DC전은 전력 정비가 먼저다.\n벨파스트의 카미유와\nZ건담을 구한다. 질문은?」",
    0x3A70: "료「적 전력은?」",
    0x3A87: "브라이트「정보상, 별일 없다.\n우리 전력으로 제압한다」",
    0x3ABD: "코우지「적이 좀 많아도,\n내가 정리해 주지!」",
    0x3AF2: "사야카「말뿐이라니까...」",
    0x3B0D: "아무로「벨파스트로 가요.\n갑시다」",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_map() -> dict[str, int]:
    """Return the injected Hangul map plus the small base glyph map we use."""
    hangul: dict[str, int] = {}
    for line in FONT_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split("\t")
        if len(fields) >= 4:
            hangul[fields[2]] = int(fields[3], 16)

    # The reviewed map's character column was generated through a legacy
    # codepage, so use its Unicode column for unambiguous punctuation.
    reviewed = json.loads(REVIEWED_MAP.read_text(encoding="utf-8"))
    base: dict[str, int] = {" ": 0, "「": 0x03E, "」": 0x03F}
    for row in reviewed["rows"]:
        unicode_field = str(row.get("unicode", ""))
        if not unicode_field.startswith("U+") or " " in unicode_field:
            continue
        try:
            character = chr(int(unicode_field[2:], 16))
        except ValueError:
            continue
        if character in ",.!?-+?!" and character not in base:
            base[character] = int(row["glyph_index"])
        elif 0x20 <= ord(character) < 0x7F and character not in base:
            # Keep the first low-font occurrence for ASCII.  The reviewed map
            # can contain duplicate OCR rows in the extended font area.
            base[character] = int(row["glyph_index"])
    missing = [char for char in ",.!?" if char not in base]
    if missing:
        raise ValueError(f"reviewed map has no punctuation glyphs: {missing}")
    base.update(hangul)
    return base


CONTROL_RE = re.compile(r"⟦F([6-9A-Ea-e]):([0-9A-Fa-f ]*)⟧")


def encode_glyph(index: int) -> bytes:
    if not 0 <= index < 0xB00:
        raise ValueError(f"glyph index outside PS-X font: {index:#x}")
    if index < 0xEB:
        return bytes((index,))
    return bytes((0xEB + (index >> 8), index & 0xFF))


def encode_text(text: str, glyph_map: dict[str, int]) -> tuple[bytes, dict[str, object]]:
    output = bytearray()
    line_glyphs = [0]
    control_count = 0
    cursor = 0
    while cursor < len(text):
        match = CONTROL_RE.match(text, cursor)
        if match:
            opcode = int(match.group(1), 16)
            args_text = match.group(2).strip()
            args = bytes.fromhex(args_text) if args_text else b""
            expected = {6: 0, 7: 2, 8: 1, 9: 1, 10: 0, 11: 2, 12: 2, 13: 2, 14: 1}[opcode]
            if len(args) != expected:
                raise ValueError(f"F{opcode:X} expects {expected} argument bytes")
            output.extend((0xF0 + opcode,))
            output.extend(args)
            control_count += 1
            if opcode == 6:
                line_glyphs.append(0)
            cursor = match.end()
            continue

        char = text[cursor]
        cursor += 1
        if char == "\n":
            output.append(0xF6)
            line_glyphs.append(0)
            continue
        if char not in glyph_map:
            raise ValueError(f"no injected/base glyph for {char!r}")
        output.extend(encode_glyph(glyph_map[char]))
        line_glyphs[-1] += 1

    if len(line_glyphs) > MAX_LINES:
        raise ValueError(f"{text!r}: {len(line_glyphs)} lines exceeds {MAX_LINES}")
    if max(line_glyphs, default=0) > MAX_LINE_GLYPHS:
        raise ValueError(
            f"{text!r}: line width {max(line_glyphs)} exceeds {MAX_LINE_GLYPHS} glyph cells"
        )
    output.append(0xFF)
    return bytes(output), {
        "line_count": len(line_glyphs),
        "line_glyph_counts": line_glyphs,
        "max_line_glyphs": max(line_glyphs, default=0),
        "control_count": control_count,
    }


def records_in_pool(data: bytes, start: int, end: int) -> list[tuple[int, int, bytes]]:
    records: list[tuple[int, int, bytes]] = []
    cursor = start
    while cursor < end:
        terminator = data.find(b"\xff", cursor, end)
        if terminator < 0:
            raise ValueError(f"unterminated record at {cursor:#x}")
        record_end = terminator + 1
        records.append((cursor, record_end, data[cursor:record_end]))
        cursor = record_end
    if cursor != end:
        raise ValueError("text pool does not end on a record boundary")
    return records


def patch_sce(
    source: bytes,
    glyph_map: dict[str, int],
    translations: dict[int, str],
    profile: str,
    preserve_layout: bool = False,
) -> tuple[bytes, dict[str, object]]:
    if len(source) != 417700:
        raise ValueError(f"unexpected 2_SCE.BIN size: {len(source)}")
    table_bytes = struct.unpack_from("<I", source, 0)[0]
    pointers = [i * 4 + struct.unpack_from("<I", source, i * 4)[0] for i in range(table_bytes // 4)]
    if pointers[0] != 0x198 or pointers[1] != 0x1FD1 or pointers[2] != 0x1FD4:
        raise ValueError(f"unexpected opening scenario pointers: {[hex(p) for p in pointers[:3]]}")
    pools: list[tuple[int, int, int, bytearray, int]] = []
    manifest_records: list[dict[str, object]] = []
    remaining = set(translations)
    for scenario in range(len(pointers) // 2):
        block_start = pointers[scenario * 2]
        pool_end = pointers[scenario * 2 + 1]
        pool_start = block_start + struct.unpack_from("<I", source, block_start)[0]
        selected = {offset for offset in remaining if pool_start <= offset < pool_end}
        if not selected:
            continue
        records = records_in_pool(source, pool_start, pool_end)
        by_start = {start: (end, raw) for start, end, raw in records}
        if selected - set(by_start):
            raise ValueError(
                "translation offsets are not record starts: "
                + ", ".join(f"{x:#x}" for x in sorted(selected - set(by_start)))
            )
        replacements: dict[int, tuple[bytes, dict[str, object]]] = {}
        for offset in selected:
            encoded, stats = encode_text(translations[offset], glyph_map)
            source_length = len(by_start[offset][1])
            if preserve_layout:
                if len(encoded) > source_length:
                    raise ValueError(
                        f"{offset:#x}: encoded dialogue is {len(encoded)} bytes, "
                        f"but its fixed slot is only {source_length} bytes"
                    )
                # Keep FF at the original slot end.  Zero is the game's blank
                # glyph, not a line advance, so padding cannot alter wrapping.
                encoded = encoded[:-1] + bytes(source_length - len(encoded)) + b"\xFF"
            replacements[offset] = (encoded, stats)
            remaining.remove(offset)
        new_pool = bytearray()
        for start, end, raw in records:
            replacement, stats = replacements.get(start, (raw, None))
            new_offset = pool_start + len(new_pool)
            new_pool.extend(replacement)
            if stats is not None:
                manifest_records.append(
                    {
                        "scenario_index": scenario,
                        "source_offset": f"0x{start:X}",
                        "source_end": f"0x{end:X}",
                        "new_offset": f"0x{new_offset:X}",
                        "source_sha256": sha256(raw),
                        "source_byte_length": len(raw),
                        "new_byte_length": len(replacement),
                        "byte_delta": len(replacement) - len(raw),
                        "japanese_raw_hex": raw.hex(" ").upper(),
                        "korean_text": translations[start],
                        **stats,
                    }
                )
        pools.append((pool_start, pool_end, scenario, new_pool, len(new_pool) - (pool_end - pool_start)))

    if remaining:
        raise ValueError("translation offsets are not record starts: " + ", ".join(f"{x:#x}" for x in sorted(remaining)))
    delta = sum(pool_delta for _start, _end, _scenario, _pool, pool_delta in pools)
    if delta < 0 or (delta == 0 and not preserve_layout):
        raise ValueError("test patch did not expand the text pool")
    if delta > 92:
        raise ValueError(f"text pool expansion {delta} exceeds final-sector slack 92")
    patched = bytearray()
    cursor = 0
    for pool_start, pool_end, _scenario, new_pool, _pool_delta in pools:
        patched.extend(source[cursor:pool_start])
        patched.extend(new_pool)
        cursor = pool_end
    patched.extend(source[cursor:])
    for index in range(len(pointers)):
        old_target = pointers[index]
        shift = sum(pool_delta for pool_start, pool_end, _scenario, _pool, pool_delta in pools if pool_end <= old_target)
        new_target = old_target + shift
        if new_target == old_target:
            continue
        field = index * 4
        struct.pack_into("<I", patched, field, new_target - field)
    # The file is still inside the same allocated ISO sectors; its logical
    # length grows by delta and the unused tail remains unchanged.
    document = {
        "format": "srwcb-second-dialogue-test-v2",
        "profile": profile,
        "source_file": str(SOURCE_SCE),
        "source_sha256": sha256(source),
        "output_sha256": sha256(bytes(patched)),
        "source_size": len(source),
        "output_size": len(patched),
        "text_pool_byte_delta": delta,
        "layout_preserved": preserve_layout,
        "patched_pools": [
            {
                "scenario_index": scenario,
                "source_start": f"0x{pool_start:X}",
                "source_end": f"0x{pool_end:X}",
                "output_end": f"0x{pool_end + pool_delta:X}",
                "byte_delta": pool_delta,
            }
            for pool_start, pool_end, scenario, _pool, pool_delta in pools
        ],
        "limits": {
            "max_display_lines": MAX_LINES,
            "max_line_glyph_cells_test": MAX_LINE_GLYPHS,
            "line_advance_opcode": "F6",
            "record_terminator": "FF",
        },
        "records": manifest_records,
    }
    return bytes(patched), document


def patch_raw_track(base_track: Path, output_track: Path, source_sce: bytes, patched_sce: bytes) -> None:
    """Replace 2_SCE.BIN in a raw MODE2 track and fix ISO size/EDC/ECC."""
    lba = 24718
    alloc_sectors = math.ceil(len(source_sce) / USER_DATA_SIZE)
    if len(patched_sce) > alloc_sectors * USER_DATA_SIZE:
        raise ValueError("patched SCE no longer fits its allocated sectors")
    output_track.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base_track, output_track)
    with output_track.open("r+b") as track:
        original_padded = bytearray()
        sectors: list[bytearray] = []
        for n in range(alloc_sectors):
            track.seek((lba + n) * SECTOR_SIZE)
            sector = bytearray(track.read(SECTOR_SIZE))
            if len(sector) != SECTOR_SIZE:
                raise ValueError("track ends in 2_SCE.BIN")
            sectors.append(sector)
            original_padded.extend(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
        if bytes(original_padded[: len(source_sce)]) != source_sce:
            raise ValueError("raw track 2_SCE.BIN does not match extracted source")
        replacement_padded = patched_sce + bytes(original_padded[len(patched_sce) :])
        for n, sector in enumerate(sectors):
            old_chunk = bytes(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE])
            new_chunk = replacement_padded[n * USER_DATA_SIZE : (n + 1) * USER_DATA_SIZE]
            if old_chunk == new_chunk:
                continue
            sector[USER_DATA_OFFSET:USER_DATA_OFFSET + USER_DATA_SIZE] = new_chunk
            rebuild_mode2_form1(sector)
            track.seek((lba + n) * SECTOR_SIZE)
            track.write(sector)

        name = b"2_SCE.BIN;1"
        track.seek(0)
        raw = track.read()
        name_offset = raw.find(name)
        if name_offset < 33:
            raise ValueError("ISO directory entry for 2_SCE.BIN;1 not found")
        entry_start = name_offset - 33
        entry_len = raw[entry_start]
        if entry_len < 33 + len(name) or raw[entry_start + 33 : name_offset + len(name)] != name:
            raise ValueError("unexpected ISO directory entry layout")
        directory_lba = entry_start // SECTOR_SIZE
        directory_offset = entry_start % SECTOR_SIZE
        track.seek(directory_lba * SECTOR_SIZE)
        directory_sector = bytearray(track.read(SECTOR_SIZE))
        if struct.unpack_from("<I", directory_sector, directory_offset + 10)[0] != len(source_sce):
            raise ValueError("ISO directory source size does not match extracted file")
        struct.pack_into("<I", directory_sector, directory_offset + 10, len(patched_sce))
        struct.pack_into(">I", directory_sector, directory_offset + 14, len(patched_sce))
        rebuild_mode2_form1(directory_sector)
        track.seek(directory_lba * SECTOR_SIZE)
        track.write(directory_sector)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("opening-test", "capture", "capture-safe"), default="opening-test")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--base-track", type=Path, default=Path("korean_patch/test_build/exe_font_test/Super Robot Taisen Complete Box Hangul Font Test (Track 1).bin"))
    args = parser.parse_args()
    if args.output_dir is None:
        args.output_dir = Path(
            "korean_patch/test_build/second_dialogue_capture_test"
            if args.profile == "capture"
            else "korean_patch/test_build/second_dialogue_safe_test"
            if args.profile == "capture-safe"
            else "korean_patch/test_build/second_dialogue_test"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    glyph_map = read_map()
    source = SOURCE_SCE.read_bytes()
    translations = (
        CAPTURE_TRANSLATIONS
        if args.profile == "capture"
        else CAPTURE_SAFE_TRANSLATIONS
        if args.profile == "capture-safe"
        else TRANSLATIONS
    )
    patched, manifest = patch_sce(
        source,
        glyph_map,
        translations,
        args.profile,
        preserve_layout=args.profile == "capture-safe",
    )
    out_sce = args.output_dir / "extracted/SECOND/2_SCE.BIN"
    out_sce.parent.mkdir(parents=True, exist_ok=True)
    out_sce.write_bytes(patched)
    (args.output_dir / "translation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readme = (
        "# 제2차 슈퍼로봇대전 앞부분 대사 테스트\n\n"
        "`SECOND/2_SCE.BIN`의 오프닝 시나리오 앞 6개 대사 레코드만 교체한 테스트입니다.\n"
        f"- 대사창 최대 표시 줄 수: {MAX_LINES}줄 (SECOND.WAR 전처리 코드에서 F6 3회 검사 확인)\n"
        f"- 테스트 한 줄 제한: {MAX_LINE_GLYPHS} glyph 칸 (일본어 원본의 수동 줄바꿈 범위에 맞춘 안전값)\n"
        "- F6 줄바꿈과 FF 레코드 종료, F7 제어코드는 보존/검증합니다.\n"
        "- 에뮬레이터 실행은 사용자가 직접 확인합니다.\n"
    )
    (args.output_dir / "README.md").write_text(readme, encoding="utf-8")
    out_track = args.output_dir / "Super Robot Taisen Complete Box Second Dialogue Test (Track 1).bin"
    patch_raw_track(args.base_track, out_track, source, patched)
    manifest["track_output"] = str(out_track)
    manifest["track_sha256"] = sha256(out_track.read_bytes())
    (args.output_dir / "translation_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_sce": str(out_sce), "output_track": str(out_track), "delta": manifest["text_pool_byte_delta"], "track_sha256": manifest["track_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
