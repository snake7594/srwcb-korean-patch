#!/usr/bin/env python3
"""Glyph encoding and lossless dialogue layout for SECOND's Korean patch."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
FONT_MAP = ROOT / "korean_patch" / "test_build" / "exe_font_safe_test" / "font" / "hangul_ksx1001_exe_mapping.tsv"
REVIEWED_MAP = ROOT / "korean_patch" / "research" / "srwcb_embedded_font_mapping_reviewed.json"

# The retail SECOND dialogue window is twenty full-width cells wide.  The
# earlier 26-cell value was inferred from the source script's Japanese byte
# spans; Korean Hangul uses the full-width renderer and consequently clipped
# the rightmost characters in every long line.  Keep this as the single
# layout limit so every rebuilt dialogue record is wrapped consistently.
MAX_LINE_CELLS = 20
# The retail renderer (RE'd at 0x8006F564) wraps a dialogue line when a
# phase-aware cursor countdown reaches the box width of 18 units.  Low-index
# glyphs (<0x101: ASCII punctuation, spaces) advance one unit; a high glyph
# advances one unit in phase 0 or two in phase 1, then toggles the phase; F6
# resets the phase.  Japanese stays phase-aligned so its auto-wraps land on
# glyph boundaries, but Korean punctuation shifts the phase and the game's
# auto-wrap then splits a two-byte glyph (dropping its lead byte -> the first
# glyph of the next box renders as garbage).  We therefore pre-break every line
# at <= this width so the game never auto-wraps a dialogue record.
MAX_LINE_ADVANCE = 18
MAX_PAGE_LINES = 3


def glyph_advance(index: int, phase: int) -> tuple[int, int]:
    """Return (advance_units, next_phase) for one glyph in the dialogue renderer."""
    if index < 0x101:
        return 1, phase
    return 1 + phase, phase ^ 1
GLYPH_COUNT = 0xB00
EXTRA_GLYPH_START = 0xA2F
EXTRA_GLYPH_END = GLYPH_COUNT - 1

# These three cells are not ordinary Japanese letters.  The UI VM uses 0x3FF
# as an invisible full-width phase spacer and 0x6FF/0x700 as the left/right
# arrows on the name-entry pages.  The original all-Hangul injection covered
# them with 릭/읏/응, which made otherwise untranslated structural cells appear
# as garbage.  Keep the retail bitmaps at these indices and move the displaced
# Hangul syllables to the dynamic tail together with the other extra glyphs.
STRUCTURAL_GLYPH_INDICES = frozenset({0x3FF, 0x6FF, 0x700})

# The retail low-font circle/cross mappings are encoded with two bytes but
# their glyph indices remain below 0x101, so the renderer advances only one
# cell.  Button selector fields need the original two-cell symbols inside an
# exact four-byte slot; allocate these two glyphs again in the dynamic range.
FORCED_FULL_WIDTH_CHARACTERS = frozenset({"○", "×"})

# These substitutions do not remove meaning.  They only choose the matching
# glyph already present in the low, unmodified portion of the game font.
CHAR_NORMALISATION = {
    "\u00a0": " ",
    "\u3000": " ",
    "\r": " ",
    "\n": " ",
    "\t": " ",
    "，": ",",
    "．": ".",
    "？": "?",
    "！": "!",
    "：": ":",
    "；": ";",
    "（": "(",
    "）": ")",
    "［": "[",
    "］": "]",
    "【": "[",
    "】": "]",
    "‘": "'",
    "’": "'",
    "·": "・",
    "ㆍ": "・",
    "~": "〜",
    "～": "〜",
    "—": "-",
    "–": "-",
    "―": "-",
    "−": "-",
}

JAPANESE_OR_HAN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def encode_glyph_index(index: int) -> bytes:
    if not 0 <= index < GLYPH_COUNT:
        raise ValueError(f"glyph index outside font: 0x{index:X}")
    if index < 0xEB:
        return bytes((index,))
    return bytes((0xEB + (index >> 8), index & 0xFF))


def load_safe_glyph_map() -> dict[str, int]:
    """Load only glyphs that remain valid after the Hangul font injection."""

    mapping: dict[str, int] = {" ": 0}
    for line in FONT_MAP.read_text(encoding="utf-8").splitlines()[1:]:
        fields = line.split("\t")
        if (
            len(fields) >= 4
            and fields[2]
            and fields[2] not in FORCED_FULL_WIDTH_CHARACTERS
            and int(fields[3], 16) not in STRUCTURAL_GLYPH_INDICES
        ):
            mapping[fields[2]] = int(fields[3], 16)

    reviewed = json.loads(REVIEWED_MAP.read_text(encoding="utf-8"))
    for row in reviewed["rows"]:
        unicode_label = str(row.get("unicode", ""))
        if not unicode_label.startswith("U+") or " " in unicode_label:
            continue
        index = int(row["glyph_index"])
        # 0x101 and above was replaced by Hangul.  Never revive a stale
        # Japanese mapping into that range.
        if index >= 0x101:
            continue
        char = chr(int(unicode_label[2:], 16))
        if char not in FORCED_FULL_WIDTH_CHARACTERS:
            mapping.setdefault(char, index)
    return mapping


def normalise_for_font(text: str) -> tuple[str, list[dict[str, Any]]]:
    output: list[str] = []
    changes: list[dict[str, Any]] = []
    for position, char in enumerate(text):
        replacement = CHAR_NORMALISATION.get(char, char)
        if replacement != char:
            changes.append({"position": position, "from": char, "to": replacement})
        output.append(replacement)
    return "".join(output), changes


def required_extra_characters(texts: Iterable[str], glyph_map: dict[str, int]) -> list[str]:
    missing = sorted({char for text in texts for char in text if char not in glyph_map})
    bad = [char for char in missing if JAPANESE_OR_HAN_RE.fullmatch(char)]
    if bad:
        rendered = " ".join(f"{char}(U+{ord(char):04X})" for char in bad)
        raise ValueError(f"Japanese/Han characters remain in Korean translation: {rendered}")
    capacity = EXTRA_GLYPH_END - EXTRA_GLYPH_START + 1
    if len(missing) > capacity:
        raise ValueError(
            f"translation needs {len(missing)} extra glyphs but only {capacity} safe font slots remain"
        )
    return missing


def add_extra_glyph_mapping(glyph_map: dict[str, int], characters: Iterable[str]) -> dict[str, int]:
    result = dict(glyph_map)
    for index, char in enumerate(characters, EXTRA_GLYPH_START):
        if char in result:
            continue
        result[char] = index
    return result


@dataclass
class LayoutState:
    glyph_map: dict[str, int]
    data: bytearray = field(default_factory=bytearray)
    pages: list[list[str]] = field(default_factory=lambda: [[""]])
    page_cell_counts: list[list[int]] = field(default_factory=lambda: [[0]])
    page_advances: list[list[int]] = field(default_factory=lambda: [[0]])
    phase: int = 0
    pending_spaces: int = 0
    inserted_line_breaks: int = 0
    inserted_page_breaks: int = 0
    preserved_page_breaks: int = 0
    whitespace_wraps: int = 0

    @property
    def current_line(self) -> str:
        return self.pages[-1][-1]

    @property
    def current_cells(self) -> int:
        return self.page_cell_counts[-1][-1]

    @property
    def current_advance(self) -> int:
        return self.page_advances[-1][-1]

    def _span_advance(self, text: str) -> int:
        """Phase-aware renderer advance of ``text`` starting from the current phase."""
        phase = self.phase
        total = 0
        for char in text:
            step, phase = glyph_advance(self.glyph_map[char], phase)
            total += step
        return total

    def _append_visual(self, text: str, cells: int, advance: int = 0) -> None:
        self.pages[-1][-1] += text
        self.page_cell_counts[-1][-1] += cells
        self.page_advances[-1][-1] += advance

    def _new_line_or_page(self) -> None:
        self.phase = 0  # the renderer resets the wide phase at every F6/F7 break
        if len(self.pages[-1]) < MAX_PAGE_LINES:
            self.data.append(0xF6)
            self.pages[-1].append("")
            self.page_cell_counts[-1].append(0)
            self.page_advances[-1].append(0)
            self.inserted_line_breaks += 1
        else:
            self.data.append(0xF7)
            self.pages.append([""])
            self.page_cell_counts.append([0])
            self.page_advances.append([0])
            self.inserted_page_breaks += 1

    def _emit_char(self, char: str) -> None:
        try:
            index = self.glyph_map[char]
        except KeyError as exc:
            raise ValueError(f"no font glyph allocated for {char!r} U+{ord(char):04X}") from exc
        advance, self.phase = glyph_advance(index, self.phase)
        self.data.extend(encode_glyph_index(index))
        self._append_visual(char, 1, advance)

    def _emit_spaces(self, count: int) -> None:
        for _ in range(count):
            if self.current_advance + 1 > MAX_LINE_ADVANCE:
                self._new_line_or_page()
            self._emit_char(" ")

    def _emit_word(self, word: str) -> None:
        for char in word:
            advance, _ = glyph_advance(self.glyph_map[char], self.phase)
            if self.current_advance and self.current_advance + advance > MAX_LINE_ADVANCE:
                self._new_line_or_page()
            self._emit_char(char)

    def emit_text(self, text: str) -> None:
        for token in re.findall(r" +|[^ ]+", text):
            if token[0] == " ":
                self.pending_spaces += len(token)
                continue

            if self.pending_spaces:
                # spaces advance one unit each (low glyph, no phase change)
                needed = self.pending_spaces + self._span_advance(token)
                if self.current_advance and self.current_advance + needed > MAX_LINE_ADVANCE:
                    # A layout break is the visible separator at a word
                    # boundary; no lexical space is deleted within a line.
                    self._new_line_or_page()
                    self.whitespace_wraps += 1
                else:
                    self._emit_spaces(self.pending_spaces)
                self.pending_spaces = 0
            elif self.current_advance and self.current_advance + self._span_advance(token) > MAX_LINE_ADVANCE:
                # There was no legal word boundary.  Split a long token rather
                # than alter or truncate it.
                self._new_line_or_page()
            self._emit_word(token)

    def emit_control(self, raw: bytes, visible_cells: int = 0) -> None:
        if self.pending_spaces:
            if self.current_advance and self.current_advance + self.pending_spaces + visible_cells > MAX_LINE_ADVANCE:
                self._new_line_or_page()
                self.whitespace_wraps += 1
            else:
                self._emit_spaces(self.pending_spaces)
            self.pending_spaces = 0
        if visible_cells and self.current_advance and self.current_advance + visible_cells > MAX_LINE_ADVANCE:
            self._new_line_or_page()
        self.data.extend(raw)
        label = "⟦" + raw.hex(" ").upper() + "⟧"
        self._append_visual(label, visible_cells, visible_cells)

    def preserve_page_break(self, raw: bytes) -> None:
        if raw != b"\xF7":
            raise ValueError(f"unexpected source page-break bytes: {raw.hex(' ')}")
        # Trailing space at an explicit page boundary is represented by the
        # boundary itself, just as a wrapped inter-word space is.
        if self.pending_spaces:
            self.pending_spaces = 0
            self.whitespace_wraps += 1
        self.data.extend(raw)
        self.pages.append([""])
        self.page_cell_counts.append([0])
        self.page_advances.append([0])
        self.phase = 0
        self.preserved_page_breaks += 1

    def finish(self) -> tuple[bytes, dict[str, Any]]:
        if self.pending_spaces:
            self._emit_spaces(self.pending_spaces)
            self.pending_spaces = 0
        if any(adv > MAX_LINE_ADVANCE for page in self.page_advances for adv in page):
            raise AssertionError("layout exceeded line-advance (box width) limit")
        if any(len(page) > MAX_PAGE_LINES for page in self.pages):
            raise AssertionError("layout exceeded lines-per-page limit")
        self.data.append(0xFF)
        return bytes(self.data), {
            "pages": self.pages,
            "page_cell_counts": self.page_cell_counts,
            "page_count": len(self.pages),
            "inserted_line_breaks": self.inserted_line_breaks,
            "inserted_page_breaks": self.inserted_page_breaks,
            "preserved_page_breaks": self.preserved_page_breaks,
            "whitespace_replaced_by_layout_break": self.whitespace_wraps,
            "max_line_cells": max((cells for page in self.page_cell_counts for cells in page), default=0),
        }


def assemble_translated_record(
    translation_parts: list[dict[str, Any]],
    ko_parts: dict[str, str],
    glyph_map: dict[str, int],
) -> tuple[bytes, dict[str, Any]]:
    state = LayoutState(glyph_map)
    normalisation: list[dict[str, Any]] = []
    original_controls: list[str] = []
    for part in translation_parts:
        kind = part["kind"]
        if kind == "text":
            part_id = part["part_id"]
            if part_id not in ko_parts or not isinstance(ko_parts[part_id], str):
                raise ValueError(f"missing Korean text part {part_id}")
            value, changes = normalise_for_font(ko_parts[part_id])
            normalisation.extend({"part_id": part_id, **change} for change in changes)
            state.emit_text(value)
        elif kind == "page_break":
            raw = bytes.fromhex(part["raw_hex"])
            original_controls.append(part["raw_hex"])
            state.preserve_page_break(raw)
        elif kind == "control":
            raw = bytes.fromhex(part["raw_hex"])
            original_controls.append(part["raw_hex"])
            # F9 inserts a player-defined name/value.  Reserve eight cells so
            # surrounding Korean text remains inside the 26-cell window.
            visible_cells = 8 if raw and raw[0] == 0xF9 else 0
            state.emit_control(raw, visible_cells)
        else:
            raise ValueError(f"unknown translation part kind: {kind!r}")
    encoded, manifest = state.finish()
    manifest["normalisation"] = normalisation
    manifest["preserved_source_controls"] = original_controls
    manifest["encoded_length"] = len(encoded)
    return encoded, manifest
