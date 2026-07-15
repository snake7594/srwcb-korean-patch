#!/usr/bin/env python3
"""Build a 2,350-glyph KS X 1001 Hangul KROM table from a BDF font.

The output matches the font format used by Super Robot Taisen Complete Box:
15 stored rows, two MSB-first bytes per row, 30 bytes per 16-pixel-wide cell.
For visual testing, the table can be written over the first 2,350 level-1
kanji slots in a *copy* of a Japanese PS1 BIOS.
"""

from __future__ import annotations

import argparse
import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

from dump_ps1_krom import (
    FONT_OFFSET,
    GLYPH_BYTES,
    GLYPH_HEIGHT,
    GLYPH_WIDTH,
    NON_KANJI_COUNT,
    build_code_map,
)


HANGUL_COUNT = 2350
HANGUL_KROM_OFFSET = FONT_OFFSET + NON_KANJI_COUNT * GLYPH_BYTES


@dataclass(frozen=True)
class BdfGlyph:
    encoding: int
    width: int
    height: int
    x_offset: int
    y_offset: int
    bitmap: tuple[int, ...]
    bitmap_bits: int


def parse_bdf(path: Path) -> dict[int, BdfGlyph]:
    lines = path.read_text(encoding="utf-8").splitlines()
    glyphs: dict[int, BdfGlyph] = {}
    position = 0
    while position < len(lines):
        if not lines[position].startswith("STARTCHAR "):
            position += 1
            continue
        position += 1
        encoding: int | None = None
        bbx: tuple[int, int, int, int] | None = None
        bitmap_rows: list[str] = []
        while position < len(lines) and lines[position] != "ENDCHAR":
            line = lines[position]
            if line.startswith("ENCODING "):
                encoding = int(line.split()[1])
            elif line.startswith("BBX "):
                _, width, height, x_offset, y_offset = line.split()
                bbx = (int(width), int(height), int(x_offset), int(y_offset))
            elif line == "BITMAP":
                position += 1
                while position < len(lines) and lines[position] != "ENDCHAR":
                    bitmap_rows.append(lines[position])
                    position += 1
                break
            position += 1
        if encoding is not None and encoding >= 0 and bbx is not None:
            width, height, x_offset, y_offset = bbx
            row_bytes = (width + 7) // 8
            if len(bitmap_rows) != height:
                raise ValueError(
                    f"U+{encoding:04X}: {len(bitmap_rows)} bitmap rows, expected {height}"
                )
            expected_hex = row_bytes * 2
            if any(len(row) != expected_hex for row in bitmap_rows):
                raise ValueError(f"U+{encoding:04X}: malformed BDF bitmap row")
            glyphs[encoding] = BdfGlyph(
                encoding=encoding,
                width=width,
                height=height,
                x_offset=x_offset,
                y_offset=y_offset,
                bitmap=tuple(int(row, 16) for row in bitmap_rows),
                bitmap_bits=row_bytes * 8,
            )
        position += 1
    return glyphs


def ks_x_1001_hangul() -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for lead in range(0xB0, 0xC9):
        for trail in range(0xA1, 0xFF):
            code = (lead << 8) | trail
            character = bytes((lead, trail)).decode("euc_kr")
            result.append((code, character))
    if len(result) != HANGUL_COUNT:
        raise AssertionError(f"built {len(result)} Hangul codes, expected {HANGUL_COUNT}")
    if result[0][1] != "\uac00" or result[-1][1] != "\ud79d":
        raise AssertionError("unexpected KS X 1001 Hangul boundaries")
    return result


def render_glyph(
    glyph: BdfGlyph,
    *,
    baseline_row: int,
    x_shift: int,
    y_shift: int,
) -> tuple[bytes, int]:
    canvas = [[0 for _ in range(GLYPH_WIDTH)] for _ in range(GLYPH_HEIGHT)]
    top = baseline_row - (glyph.y_offset + glyph.height - 1) + y_shift
    left = glyph.x_offset + x_shift
    clipped = 0
    for source_y, row_bits in enumerate(glyph.bitmap):
        target_y = top + source_y
        for source_x in range(glyph.width):
            mask = 1 << (glyph.bitmap_bits - 1 - source_x)
            if not row_bits & mask:
                continue
            target_x = left + source_x
            if 0 <= target_x < GLYPH_WIDTH and 0 <= target_y < GLYPH_HEIGHT:
                canvas[target_y][target_x] = 1
            else:
                clipped += 1

    output = bytearray()
    for row in canvas:
        word = 0
        for x, pixel in enumerate(row):
            if pixel:
                word |= 0x8000 >> x
        output.extend(word.to_bytes(2, "big"))
    return bytes(output), clipped


def render_sheet(font: bytes, columns: int) -> Image.Image:
    rows = math.ceil(HANGUL_COUNT / columns)
    image = Image.new("L", (columns * GLYPH_WIDTH, rows * GLYPH_HEIGHT), 0)
    pixels = image.load()
    for glyph_index in range(HANGUL_COUNT):
        glyph = font[
            glyph_index * GLYPH_BYTES : (glyph_index + 1) * GLYPH_BYTES
        ]
        left = (glyph_index % columns) * GLYPH_WIDTH
        top = (glyph_index // columns) * GLYPH_HEIGHT
        for y in range(GLYPH_HEIGHT):
            word = int.from_bytes(glyph[y * 2 : y * 2 + 2], "big")
            for x in range(GLYPH_WIDTH):
                if word & (0x8000 >> x):
                    pixels[left + x, top + y] = 255
    return image


def write_mapping(path: Path, hangul: list[tuple[int, str]]) -> None:
    krom_codes = build_code_map()
    rows = [
        "hangul_index\teuc_kr\tcharacter\tkrom_index\tsjis_slot\tbios_offset"
    ]
    for index, (euc_kr, character) in enumerate(hangul):
        krom_index = NON_KANJI_COUNT + index
        sjis_slot = krom_codes[krom_index]
        bios_offset = FONT_OFFSET + krom_index * GLYPH_BYTES
        rows.append(
            f"{index}\t0x{euc_kr:04X}\t{character}\t{krom_index}\t"
            f"0x{sjis_slot:04X}\t0x{bios_offset:05X}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bdf", type=Path, help="source BDF font")
    parser.add_argument("--font-bin", type=Path, required=True)
    parser.add_argument("--sheet", type=Path, required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--bios", type=Path, help="source Japanese BIOS")
    parser.add_argument(
        "--patched-bios",
        type=Path,
        help="write a patched BIOS copy for visual testing",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing --patched-bios output",
    )
    parser.add_argument("--columns", type=int, default=64)
    parser.add_argument(
        "--baseline-row",
        type=int,
        default=14,
        help="cell row occupied by BDF baseline y=0 (default: 14)",
    )
    parser.add_argument("--x-shift", type=int, default=0)
    parser.add_argument("--y-shift", type=int, default=0)
    args = parser.parse_args()
    if (args.bios is None) != (args.patched_bios is None):
        parser.error("--bios and --patched-bios must be supplied together")
    if args.bios and args.patched_bios:
        source_bios = args.bios.resolve()
        output_bios = args.patched_bios.resolve()
        if source_bios == output_bios:
            parser.error("--patched-bios must not overwrite --bios")
        if output_bios.exists() and not args.force:
            parser.error("--patched-bios already exists; pass --force to overwrite it")
    if args.columns <= 0:
        parser.error("--columns must be positive")

    bdf_glyphs = parse_bdf(args.bdf)
    hangul = ks_x_1001_hangul()
    missing = [character for _, character in hangul if ord(character) not in bdf_glyphs]
    if missing:
        raise ValueError(f"BDF is missing {len(missing)} required Hangul glyphs")

    output = bytearray()
    clipped_total = 0
    for _, character in hangul:
        rendered, clipped = render_glyph(
            bdf_glyphs[ord(character)],
            baseline_row=args.baseline_row,
            x_shift=args.x_shift,
            y_shift=args.y_shift,
        )
        output.extend(rendered)
        clipped_total += clipped
    if len(output) != HANGUL_COUNT * GLYPH_BYTES:
        raise AssertionError("unexpected font output size")
    if clipped_total:
        raise ValueError(f"rendering clipped {clipped_total} foreground pixels")

    args.font_bin.parent.mkdir(parents=True, exist_ok=True)
    args.font_bin.write_bytes(output)
    write_mapping(args.mapping, hangul)

    sheet = render_sheet(output, args.columns)
    metadata = PngInfo()
    metadata.add_text("Source BDF SHA-256", hashlib.sha256(args.bdf.read_bytes()).hexdigest())
    metadata.add_text("Encoding", "KS X 1001 Hangul rows 16-40 (EUC-KR B0A1-C8FE)")
    metadata.add_text("Glyph format", "2350 glyphs, 16x15 storage, 1bpp, 30 bytes/glyph")
    metadata.add_text("Baseline row", str(args.baseline_row))
    args.sheet.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.sheet, pnginfo=metadata, optimize=True)

    if args.bios and args.patched_bios:
        bios = bytearray(args.bios.read_bytes())
        end = HANGUL_KROM_OFFSET + len(output)
        if len(bios) < end:
            raise ValueError("BIOS is too small for its KROM region")
        original_hash = hashlib.sha256(bios).hexdigest()
        bios[HANGUL_KROM_OFFSET:end] = output
        args.patched_bios.parent.mkdir(parents=True, exist_ok=True)
        args.patched_bios.write_bytes(bios)
        print(f"Source BIOS SHA-256: {original_hash}")
        print(f"Patched BIOS SHA-256: {hashlib.sha256(bios).hexdigest()}")
        print(
            f"Patched KROM: 0x{HANGUL_KROM_OFFSET:X}-0x{end - 1:X} "
            f"({HANGUL_COUNT} slots)"
        )

    print(f"BDF glyphs: {len(bdf_glyphs)}")
    print(f"Hangul glyphs: {HANGUL_COUNT}, clipped pixels: {clipped_total}")
    print(f"Font BIN: {args.font_bin} ({len(output)} bytes)")
    print(f"Font SHA-256: {hashlib.sha256(output).hexdigest()}")
    print(f"Sheet: {args.sheet} ({sheet.width}x{sheet.height})")
    print(f"Mapping: {args.mapping}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
