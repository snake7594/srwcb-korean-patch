#!/usr/bin/env python3
"""Dump the Japanese PS1 BIOS KROM font to a packed PNG sheet.

Super Robot Taisen Complete Box calls BIOS service B0:51 (Krom2RawAdd), so
its Japanese glyph bitmaps live in the Japanese console BIOS rather than in
the game disc.  Retail Japanese BIOSes store 524 JIS non-kanji glyphs followed
by 2,965 level-1 kanji glyphs at file offset 0x66000.  Each glyph is a 16x15
one-bit bitmap stored as two MSB-first bytes per row (30 bytes total).
"""

from __future__ import annotations

import argparse
import hashlib
import math
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo


FONT_OFFSET = 0x66000
GLYPH_WIDTH = 16
GLYPH_HEIGHT = 15
GLYPH_BYTES = 30
NON_KANJI_COUNT = 524
LEVEL1_KANJI_COUNT = 2965
GLYPH_COUNT = NON_KANJI_COUNT + LEVEL1_KANJI_COUNT

# (first Shift-JIS code in a contiguous run, first KROM index in that run).
# This is the non-kanji conversion table embedded in the Japanese PS1 BIOS.
NON_KANJI_RUNS = [
    (0x8140, 0x000),
    (0x8180, 0x03F),
    (0x81B8, 0x06C),
    (0x81C8, 0x074),
    (0x81DA, 0x07B),
    (0x81F0, 0x08A),
    (0x81FC, 0x092),
    (0x824F, 0x093),
    (0x8260, 0x09D),
    (0x8281, 0x0B7),
    (0x829F, 0x0D1),
    (0x8340, 0x124),
    (0x8380, 0x163),
    (0x839F, 0x17A),
    (0x83BF, 0x192),
    (0x8440, 0x1AA),
    (0x8470, 0x1CB),
    (0x8480, 0x1DA),
    (0x849F, 0x1EC),
]


def advance_shift_jis(code: int) -> int:
    lead, trail = divmod(code, 0x100)
    trail += 1
    if trail == 0x7F:
        trail = 0x80
    if trail > 0xFC:
        lead += 1
        trail = 0x40
    return (lead << 8) | trail


def build_code_map() -> list[int]:
    codes: list[int] = []
    for run_index, (start_code, first_index) in enumerate(NON_KANJI_RUNS):
        if run_index + 1 < len(NON_KANJI_RUNS):
            next_index = NON_KANJI_RUNS[run_index + 1][1]
        else:
            next_index = NON_KANJI_COUNT
        codes.extend(range(start_code, start_code + next_index - first_index))

    # Level-1 kanji are in JIS row order.  Each complete row contains 94
    # Shift-JIS codes; trail byte 0x7f is skipped.
    level1_starts = [0x889F]
    for lead in range(0x89, 0x98):
        level1_starts.extend(((lead << 8) | 0x40, (lead << 8) | 0x9F))
    level1_starts.append(0x9840)
    for row_index, start_code in enumerate(level1_starts):
        row_count = 94 if row_index + 1 < len(level1_starts) else 51
        code = start_code
        for _ in range(row_count):
            codes.append(code)
            code = advance_shift_jis(code)
        if row_index + 1 < len(level1_starts):
            expected = level1_starts[row_index + 1]
            if code != expected:
                raise AssertionError(
                    f"Shift-JIS row ended at 0x{code:04X}, expected 0x{expected:04X}"
                )

    if len(codes) != GLYPH_COUNT:
        raise AssertionError(f"built {len(codes)} codes, expected {GLYPH_COUNT}")
    return codes


def render_sheet(font: bytes, columns: int) -> Image.Image:
    rows = math.ceil(GLYPH_COUNT / columns)
    image = Image.new("L", (columns * GLYPH_WIDTH, rows * GLYPH_HEIGHT), 0)
    pixels = image.load()
    for glyph_index in range(GLYPH_COUNT):
        glyph = font[
            glyph_index * GLYPH_BYTES : (glyph_index + 1) * GLYPH_BYTES
        ]
        left = (glyph_index % columns) * GLYPH_WIDTH
        top = (glyph_index // columns) * GLYPH_HEIGHT
        for y in range(GLYPH_HEIGHT):
            row_bits = int.from_bytes(glyph[y * 2 : y * 2 + 2], "big")
            for x in range(GLYPH_WIDTH):
                if row_bits & (0x8000 >> x):
                    pixels[left + x, top + y] = 255
    return image


def write_mapping(path: Path, codes: list[int]) -> None:
    rows = ["index\tsjis\tcharacter\tbios_offset\tgroup"]
    for index, code in enumerate(codes):
        character = code.to_bytes(2, "big").decode("shift_jis")
        # Keep the TSV structurally safe even for whitespace/control-like glyphs.
        if character == "\t":
            character = "\\t"
        elif character == "\n":
            character = "\\n"
        elif character == "\r":
            character = "\\r"
        group = "non-kanji" if index < NON_KANJI_COUNT else "level1-kanji"
        offset = FONT_OFFSET + index * GLYPH_BYTES
        rows.append(f"{index}\t0x{code:04X}\t{character}\t0x{offset:05X}\t{group}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bios", type=Path, help="Japanese PS1 BIOS image")
    parser.add_argument("output", type=Path, help="output PNG path")
    parser.add_argument("--mapping", type=Path, help="optional Shift-JIS TSV path")
    parser.add_argument("--columns", type=int, default=64)
    parser.add_argument(
        "--scale",
        type=int,
        default=1,
        help="integer nearest-neighbour scale for easier viewing",
    )
    args = parser.parse_args()
    if args.columns <= 0 or args.scale <= 0:
        parser.error("--columns and --scale must be positive")

    bios = args.bios.read_bytes()
    required = FONT_OFFSET + GLYPH_COUNT * GLYPH_BYTES
    if len(bios) < required:
        raise ValueError(
            f"BIOS is too small ({len(bios)} bytes); need at least {required} bytes"
        )
    font = bios[FONT_OFFSET:required]
    sheet = render_sheet(font, args.columns)
    if args.scale != 1:
        sheet = sheet.resize(
            (sheet.width * args.scale, sheet.height * args.scale),
            Image.Resampling.NEAREST,
        )

    metadata = PngInfo()
    metadata.add_text("Source BIOS SHA-256", hashlib.sha256(bios).hexdigest())
    metadata.add_text("KROM offset", f"0x{FONT_OFFSET:X}")
    metadata.add_text("Glyph format", "3489 glyphs, 16x15, 1bpp, 30 bytes/glyph")
    metadata.add_text("Sheet columns", str(args.columns))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, pnginfo=metadata, optimize=True)

    if args.mapping:
        write_mapping(args.mapping, build_code_map())

    print(f"BIOS SHA-256: {hashlib.sha256(bios).hexdigest()}")
    print(f"Font bytes: {len(font)} (0x{FONT_OFFSET:X}-0x{required - 1:X})")
    print(f"Glyphs: {GLYPH_COUNT} ({NON_KANJI_COUNT} + {LEVEL1_KANJI_COUNT})")
    print(f"PNG: {args.output} ({sheet.width}x{sheet.height})")
    if args.mapping:
        print(f"Mapping: {args.mapping}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
