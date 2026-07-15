#!/usr/bin/env python3
"""Build and inject a 2,350-glyph Hangul test font into SRW Complete Box EXEs.

The five game executables contain the same 2,816-glyph bitmap table. Each
glyph occupies a 16x16, one-bit cell (32 bytes, two MSB-first bytes per row).
Indices 0x000-0x100 are kept intact for the game's half-width symbols and
kana. KS X 1001 Hangul is installed in indices 0x101-0xA2E, which are all
rendered through the game's full-width path.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path

from PIL import Image
from PIL.PngImagePlugin import PngInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_hangul_krom import BdfGlyph, ks_x_1001_hangul, parse_bdf


GLYPH_WIDTH = 16
GLYPH_HEIGHT = 16
GLYPH_BYTES = 32
GLYPH_COUNT = 0xB00
FONT_BYTES = GLYPH_COUNT * GLYPH_BYTES
HANGUL_START_INDEX = 0x101
HANGUL_COUNT = 2350
HANGUL_END_INDEX = HANGUL_START_INDEX + HANGUL_COUNT - 1
SOURCE_FONT_SHA256 = (
    "6d84a02c49592abc9b0a7d66d91b5aa132543090a2698ca45af001ad3aea3752"
)


EXE_LAYOUT = {
    Path("SLPS_020.70"): 0x1EDB8,
    Path("TR.WAR"): 0x1D520,
    Path("EX/EX.WAR"): 0x1D544,
    Path("SECOND/SECOND.WAR"): 0x28058,
    Path("THIRD/THIRD.WAR"): 0x2872C,
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def render_glyph(glyph: BdfGlyph) -> bytes:
    """Render Galmuri at its native 14-pixel height inside a 16x16 cell."""

    canvas = [[0 for _ in range(GLYPH_WIDTH)] for _ in range(GLYPH_HEIGHT)]
    baseline_row = 14
    top = baseline_row - (glyph.y_offset + glyph.height - 1)
    left = glyph.x_offset
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
    if clipped:
        raise ValueError(
            f"U+{glyph.encoding:04X} clipped {clipped} foreground pixels"
        )

    output = bytearray()
    for row in canvas:
        word = 0
        for x, pixel in enumerate(row):
            if pixel:
                word |= 0x8000 >> x
        output.extend(word.to_bytes(2, "big"))
    return bytes(output)


def render_sheet(font: bytes, glyph_count: int, columns: int) -> Image.Image:
    rows = math.ceil(glyph_count / columns)
    image = Image.new("L", (columns * GLYPH_WIDTH, rows * GLYPH_HEIGHT), 0)
    pixels = image.load()
    for glyph_index in range(glyph_count):
        glyph = font[glyph_index * GLYPH_BYTES : (glyph_index + 1) * GLYPH_BYTES]
        left = (glyph_index % columns) * GLYPH_WIDTH
        top = (glyph_index // columns) * GLYPH_HEIGHT
        for y in range(GLYPH_HEIGHT):
            word = int.from_bytes(glyph[y * 2 : y * 2 + 2], "big")
            for x in range(GLYPH_WIDTH):
                if word & (0x8000 >> x):
                    pixels[left + x, top + y] = 255
    return image


def save_sheet(
    path: Path,
    font: bytes,
    glyph_count: int,
    columns: int,
    metadata: dict[str, str],
) -> None:
    image = render_sheet(font, glyph_count, columns)
    pnginfo = PngInfo()
    for key, value in metadata.items():
        pnginfo.add_text(key, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, pnginfo=pnginfo, optimize=True)


def encode_glyph_index(index: int) -> bytes:
    if not 0 <= index < GLYPH_COUNT:
        raise ValueError(f"glyph index out of range: 0x{index:X}")
    if index < 0xEB:
        return bytes((index,))
    return bytes((0xEB + (index >> 8), index & 0xFF))


def write_mapping(path: Path, hangul: list[tuple[int, str]]) -> None:
    rows = [
        "hangul_index\teuc_kr\tcharacter\tglyph_index\tmessage_bytes\tfont_offset"
    ]
    for hangul_index, (euc_kr, character) in enumerate(hangul):
        glyph_index = HANGUL_START_INDEX + hangul_index
        encoded = encode_glyph_index(glyph_index).hex(" ").upper()
        font_offset = glyph_index * GLYPH_BYTES
        rows.append(
            f"{hangul_index}\t0x{euc_kr:04X}\t{character}\t0x{glyph_index:03X}\t"
            f"{encoded}\t0x{font_offset:05X}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bdf", type=Path, help="Galmuri14 BDF source")
    parser.add_argument("extracted_dir", type=Path, help="directory with the five EXEs")
    parser.add_argument("output_dir", type=Path, help="output directory")
    parser.add_argument("--columns", type=int, default=64)
    args = parser.parse_args()
    if args.columns <= 0:
        parser.error("--columns must be positive")

    glyphs = parse_bdf(args.bdf)
    hangul = ks_x_1001_hangul()
    missing = [character for _, character in hangul if ord(character) not in glyphs]
    if missing:
        raise ValueError(f"BDF is missing {len(missing)} KS X 1001 Hangul glyphs")

    hangul_font = b"".join(render_glyph(glyphs[ord(character)]) for _, character in hangul)
    if len(hangul_font) != HANGUL_COUNT * GLYPH_BYTES:
        raise AssertionError("unexpected Hangul font size")

    source_fonts: list[bytes] = []
    source_exes: dict[Path, bytes] = {}
    for relative_path, font_offset in EXE_LAYOUT.items():
        source_path = args.extracted_dir / relative_path
        data = source_path.read_bytes()
        font = data[font_offset : font_offset + FONT_BYTES]
        if len(font) != FONT_BYTES:
            raise ValueError(f"{source_path}: truncated font table")
        if sha256(font) != SOURCE_FONT_SHA256:
            raise ValueError(
                f"{source_path}: unexpected font SHA-256 {sha256(font)}"
            )
        source_fonts.append(font)
        source_exes[relative_path] = data
    if any(font != source_fonts[0] for font in source_fonts[1:]):
        raise ValueError("the five source font tables are not identical")

    patched_font = bytearray(source_fonts[0])
    start = HANGUL_START_INDEX * GLYPH_BYTES
    end = start + len(hangul_font)
    patched_font[start:end] = hangul_font

    args.output_dir.mkdir(parents=True, exist_ok=True)
    font_dir = args.output_dir / "font"
    font_dir.mkdir(parents=True, exist_ok=True)
    (font_dir / "hangul_galmuri14_ksx1001_16x16.bin").write_bytes(hangul_font)
    (font_dir / "srwcb_font_hangul_test_2816_16x16.bin").write_bytes(patched_font)
    write_mapping(font_dir / "hangul_ksx1001_exe_mapping.tsv", hangul)

    common_metadata = {
        "Source BDF SHA-256": sha256(args.bdf.read_bytes()),
        "Glyph format": "16x16, 1bpp, 32 bytes/glyph, row-major MSB-first",
        "Hangul range": f"0x{HANGUL_START_INDEX:03X}-0x{HANGUL_END_INDEX:03X}",
    }
    save_sheet(
        font_dir / "srwcb_original_font_2816_16x16.png",
        source_fonts[0],
        GLYPH_COUNT,
        args.columns,
        {
            "Source font SHA-256": SOURCE_FONT_SHA256,
            "Glyph format": common_metadata["Glyph format"],
        },
    )
    save_sheet(
        font_dir / "hangul_galmuri14_ksx1001_16x16.png",
        hangul_font,
        HANGUL_COUNT,
        args.columns,
        common_metadata,
    )
    save_sheet(
        font_dir / "srwcb_font_hangul_test_2816_16x16.png",
        patched_font,
        GLYPH_COUNT,
        args.columns,
        common_metadata,
    )

    patched_root = args.output_dir / "extracted"
    for relative_path, font_offset in EXE_LAYOUT.items():
        data = bytearray(source_exes[relative_path])
        data[font_offset : font_offset + FONT_BYTES] = patched_font
        output_path = patched_root / relative_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(data)
        print(
            f"{relative_path}: font@0x{font_offset:X}, "
            f"SHA-256 {sha256(data)}"
        )

    print(f"Source font SHA-256: {SOURCE_FONT_SHA256}")
    print(f"Hangul glyphs: {HANGUL_COUNT} (0x{HANGUL_START_INDEX:03X}-0x{HANGUL_END_INDEX:03X})")
    print(f"Hangul font SHA-256: {sha256(hangul_font)}")
    print(f"Patched font SHA-256: {sha256(patched_font)}")
    print(f"Output: {args.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
