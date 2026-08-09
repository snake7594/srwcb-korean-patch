#!/usr/bin/env python3
"""Glyph encoding and lossless dialogue layout for SECOND's Korean patch."""

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

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


ROOT = _P.WORK
FONT_MAP = _P.BUILD / "exe_font_safe_test" / "font" / "hangul_ksx1001_exe_mapping.tsv"
REVIEWED_MAP = _P.FONT_MAPPING

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
# 대사창 상자의 실제 폭(칸). 시나리오 대사는 이 값으로 접는다.
MAX_SCENE_ADVANCE = 32
MAX_PAGE_LINES = 3
# 어떤 낱말도 들어가는 넓이 (마지막 수단)
SAFE_FALLBACK_ADVANCE = 32
# 제어코드가 뒤에 데리고 오는 인자 바이트 수
CONTROL_ARGUMENT_BYTES = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0,
                          0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}


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
    # 대사 상자 크기. 레트일 레코드가 실제로 쓴 값을 넣어 준다 — 게임의 상자는
    # 장면마다 크기가 달라서(폭 16~32, 페이지당 1~3줄) 고정값을 쓰면 넘친다.
    max_advance: int = MAX_LINE_ADVANCE
    max_lines: int = MAX_PAGE_LINES
    # 시나리오 대사 원문에는 F7(쪽 나눔)이 **한 개도 없다**. 우리가 넣은 F7 이
    # 대사를 밀리게 만든다. 대사에서는 False 로 두고 F6 만 쓴다.
    allow_page_break: bool = True
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
    strict_width: bool = True

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
        if len(self.pages[-1]) < self.max_lines or not self.allow_page_break:
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
            if self.current_advance + 1 > self.max_advance:
                self._new_line_or_page()
            self._emit_char(" ")

    def _emit_word(self, word: str) -> None:
        for char in word:
            advance, _ = glyph_advance(self.glyph_map[char], self.phase)
            if self.current_advance and self.current_advance + advance > self.max_advance:
                self._new_line_or_page()
            self._emit_char(char)

    def emit_text(self, text: str) -> None:
        for token in re.findall(r" +|[^ ]+", text):
            if token[0] == " ":
                self.pending_spaces += len(token)
                continue

            # 낱말 자체가 한 줄보다 길면 줄을 바꿔도 어차피 중간에서 잘린다.
            # 그때 줄을 바꾸면 지금 줄의 남은 칸을 통째로 버리게 된다 —
            # 띄어쓰기를 지운 뒤 문장이 한 덩어리가 되면 첫 줄이 12칸만 쓰이고
            # 넉 줄이 되어 상자를 넘치던 원인이 이것이다.
            span = self._span_advance(token)
            fits_alone = span <= self.max_advance
            if self.pending_spaces:
                # spaces advance one unit each (low glyph, no phase change)
                needed = self.pending_spaces + span
                if (self.current_advance and fits_alone
                        and self.current_advance + needed > self.max_advance):
                    # A layout break is the visible separator at a word
                    # boundary; no lexical space is deleted within a line.
                    self._new_line_or_page()
                    self.whitespace_wraps += 1
                else:
                    self._emit_spaces(self.pending_spaces)
                self.pending_spaces = 0
            elif (self.current_advance and fits_alone
                    and self.current_advance + span > self.max_advance):
                # There was no legal word boundary.  Split a long token rather
                # than alter or truncate it.
                self._new_line_or_page()
            self._emit_word(token)

    def emit_control(self, raw: bytes, visible_cells: int = 0) -> None:
        if self.pending_spaces:
            if self.current_advance and self.current_advance + self.pending_spaces + visible_cells > self.max_advance:
                self._new_line_or_page()
                self.whitespace_wraps += 1
            else:
                self._emit_spaces(self.pending_spaces)
            self.pending_spaces = 0
        if visible_cells and self.current_advance and self.current_advance + visible_cells > self.max_advance:
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
        # 끝에 남은 공백은 버린다. 화면에는 안 보이면서 줄 폭만 잡아먹어
        # 상자를 한 칸 넘기게 만든다.
        self.pending_spaces = 0
        if self.strict_width and any(
                adv > self.max_advance for page in self.page_advances for adv in page):
            raise AssertionError("layout exceeded line-advance (box width) limit")
        if self.allow_page_break and any(len(page) > self.max_lines for page in self.pages):
            raise AssertionError("layout exceeded lines-per-page limit")
        self.data.append(0xFF)
        return bytes(self.data), {
            "pages": self.pages,
            "page_advances": self.page_advances,
            "page_cell_counts": self.page_cell_counts,
            "page_count": len(self.pages),
            "inserted_line_breaks": self.inserted_line_breaks,
            "inserted_page_breaks": self.inserted_page_breaks,
            "preserved_page_breaks": self.preserved_page_breaks,
            "whitespace_replaced_by_layout_break": self.whitespace_wraps,
            "max_line_cells": max((cells for page in self.page_cell_counts for cells in page), default=0),
        }


def fit_exact_length(encoded: bytes, want: int) -> bytes:
    """레코드를 원문과 **정확히 같은 바이트 길이**로 만든다.

    엔진은 포인터가 없는 대사를 '풀 시작 + 원문 기준 바이트 오프셋'으로 찾는다.
    앞선 레코드가 원문보다 길거나 짧으면 그만큼 밀려서, 대사가 문장 중간부터
    나오고 화자가 사라진다(제3차 8화). 그래서 풀 배치를 원문과 바이트 단위로
    똑같이 맞춰야 한다.

    길면 글리프 경계에서 자르고, 짧으면 종결자 앞을 반각 공백으로 채운다.
    """
    if want < 1:
        return encoded
    body = bytes(encoded)
    if body and body[-1] == 0xFF:
        body = body[:-1]
    # 글리프 경계를 지키며 want-1 바이트까지만 남긴다
    keep = 0
    q = 0
    while q < len(body):
        b = body[q]
        n = 1 if b < 0xEB else (2 if b <= 0xF5 else 1 + CONTROL_ARGUMENT_BYTES.get(b, 0))
        if q + n > want - 1:
            break
        q += n
        keep = q
    out = bytearray(body[:keep])
    need = (want - 1) - len(out)
    if need > 0:
        # 채움 바이트(0x00)도 **글리프라서 폭을 먹는다.** 그냥 붙이면 마지막 줄이
        # 상자(32칸)를 넘어 화면이 깨진다. 그래서 줄이 찰 때마다 F6(줄바꿈)을
        # 넣어 다음 줄로 넘긴다 — 빈 줄이라 화면에는 안 보인다.
        adv, ln = _tail_line_state(out)
        while need > 0:
            if adv >= MAX_SCENE_ADVANCE and ln < MAX_PAGE_LINES:
                out.append(0xF6); need -= 1; adv = 0; ln += 1
                continue
            out.append(0x00); need -= 1; adv += 1
    out.append(0xFF)
    return bytes(out)


def _tail_line_state(buf: bytes) -> tuple[int, int]:
    """레코드 바이트열의 **마지막 줄 advance** 와 그 쪽의 줄 수를 센다."""
    adv = 0
    lines = 1
    phase = 0
    i = 0
    while i < len(buf):
        x = buf[i]
        if x == 0xFF:
            break
        if x < 0xEB:
            idx = x
            i += 1
        elif x < 0xF6:
            idx = ((x - 0xEB) << 8) | (buf[i + 1] if i + 1 < len(buf) else 0)
            i += 2
        else:
            if x == 0xF6:
                adv = 0
                phase = 0
                lines += 1
            elif x == 0xF7:
                adv = 0
                phase = 0
                lines = 1
            i += 1 + CONTROL_ARGUMENT_BYTES.get(x, 0)
            continue
        if idx < 0x101:
            adv += 1
        else:
            adv += 1 + phase
            phase ^= 1
    return adv, lines


def pin_objective_block(src_sce, replacements, *, label="", verbose=True):
    """작전목적(승리/패배조건) 블록 레코드를 **원문과 똑같은 바이트 길이**로 고정.

    작전목적 화면이 이 레코드들을 어떤 방식으로 집어 오는지(순번인지 바이트
    오프셋인지)에 관계없이 안전하도록, 시나리오 앞머리 블록만 레트일 배치를
    그대로 유지한다. 대사는 이 블록 밖이라 자유롭게 커진다.

    한 줄짜리 조건문이라 예산 초과는 드물고(제3차 81/432), 넘치면 앞단에서
    띄어쓰기를 지운 뒤 남는 만큼만 잘린다.
    """
    from analyze_sce_relocation import parse_scenarios, objective_block_records
    src = bytes(src_sce)
    block = objective_block_records(src)
    want = {r.start: r.end - r.start
            for s in parse_scenarios(src) for r in s.records}
    pinned = cut = lost = 0
    for off in list(replacements):
        if off not in block:
            continue
        size = want.get(off)
        if not size or len(replacements[off]) == size:
            continue
        if len(replacements[off]) > size:
            cut += 1
            lost += len(replacements[off]) - size
        replacements[off] = fit_exact_length(replacements[off], size)
        pinned += 1
    if verbose and pinned:
        print(f"  작전목적 블록 고정{label}: {pinned}개 "
              f"(길이 초과로 줄인 것 {cut}개, {lost}B)")
    return replacements


def record_geometry(raw: bytes) -> tuple[int, int]:
    """레트일 레코드가 실제로 쓴 (줄 폭, 페이지당 줄수).

    일본어 원문은 그 장면의 상자 안에 들어가도록 쓰여 있으니, 거기서 읽은 값은
    '이만큼은 확실히 들어간다'는 하한이다. 한국어를 이 안에 맞추면 넘칠 일이 없다.
    """
    adv = 0
    max_adv = 0
    lines = 1
    max_lines = 1
    phase = 0
    p = 0
    n = len(raw)
    while p < n:
        b = raw[p]
        if b == 0xFF:
            break
        if b < 0xEB:
            index = b
            p += 1
        elif b <= 0xF5:
            index = (b - 0xEB) << 8 | raw[p + 1]
            p += 2
        else:
            if b in (0xF6, 0xF7):
                max_adv = max(max_adv, adv)
                adv = 0
                phase = 0
                if b == 0xF6:
                    lines += 1
                    max_lines = max(max_lines, lines)
                else:
                    lines = 1
            p += 1 + CONTROL_ARGUMENT_BYTES.get(b, 0)
            continue
        step, phase = glyph_advance(index, phase)
        adv += step
    max_adv = max(max_adv, adv)
    return max_adv, max_lines


def assemble_translated_record(
    translation_parts: list[dict[str, Any]],
    ko_parts: dict[str, str],
    glyph_map: dict[str, int],
    geometry: tuple[int, int] | None = None,
    max_bytes: int | None = None,
) -> tuple[bytes, dict[str, Any]]:
    adv, lines, page_break = (geometry or (MAX_LINE_ADVANCE, MAX_PAGE_LINES, True))
    if page_break:
        # 원문이 쪽을 나눈 레코드 — 우리도 나눠도 된다.
        for squeeze in (0, 1, 2, 3, 4, 5):
            enc, man = _assemble(translation_parts, ko_parts, glyph_map,
                                 adv, lines, True, squeeze, strict=False)
            widest = max((a for pg in man["page_advances"] for a in pg), default=0)
            if widest <= adv:
                return enc, man
        return _assemble(translation_parts, ko_parts, glyph_map,
                         max(adv, SAFE_FALLBACK_ADVANCE), lines, True, 1)

    # 원문이 쪽을 안 나눈 레코드 — 대사창 경로는 F7 을 처리하지 못한다.
    # 바이트 상한이 주어지면 그 안에 들어갈 때까지 더 줄인다.
    # 상자(기본 32x3 = 96칸) 안에 넣는다. 안 되면 띄어쓰기를 지운다.
    first = None
    best = None                     # 상자에는 들어가는 것 중 가장 짧은 것
    for squeeze in (0, 1, 2, 3, 4, 5):
        enc, man = _assemble(translation_parts, ko_parts, glyph_map,
                             adv, lines, False, squeeze, strict=False)
        widest = max((a for pg in man["page_advances"] for a in pg), default=0)
        if first is None:
            first = (enc, man)
        fits_box = (widest <= adv and max(len(pg) for pg in man["pages"]) <= lines)
        if fits_box and (max_bytes is None or len(enc) <= max_bytes):
            return enc, man
        if fits_box and (best is None or len(enc) < len(best[0])):
            best = (enc, man)
    # 바이트 예산에는 못 들어간 대사.  제3차는 레코드가 원문 자리에 있어야 하므로
    # (엔진이 포인터 없는 대사를 원문 바이트 오프셋으로 찾는다) **상자에는 들어가는
    # 것 중 가장 짧은 후보**를 돌려준다. 예전에는 무조건 축약단계 4 를 썼는데,
    # 5(화자 이름 생략)가 더 짧은 경우를 놓쳐 쓸데없이 잘려 나갔다.
    if best is not None:
        enc, man = best
    else:
        enc, man = _assemble(translation_parts, ko_parts, glyph_map,
                             adv, lines, False, 5, strict=False)
    man["overflow"] = True
    return enc, man
    return _assemble(translation_parts, ko_parts, glyph_map, adv, lines, page_break, 0)


# 뜻·말투를 유지하는 한국어 축약 (긴 것부터)
CONTRACTIONS = [
    ("무엇을", "뭘"), ("무엇이", "뭐가"), ("무엇", "뭐"),
    ("이야기", "얘기"), ("그리하여", "그래서"), ("그러하다", "그렇다"),
    ("것입니다", "겁니다"), ("것이다", "거다"), ("것이야", "거야"), ("것이", "게"),
    ("것은", "건"), ("것을", "걸"), ("것도", "것도"), ("것과", "것과"),
    ("하여야", "해야"), ("되어야", "돼야"), ("하여", "해"), ("되어", "돼"),
    ("말이야", "말야"), ("말이지", "말지"),
    ("나는", "난"), ("너는", "넌"), ("저는", "전"), ("우리는", "우린"),
    ("그것은", "그건"), ("그것을", "그걸"), ("이것은", "이건"), ("이것을", "이걸"),
    ("저것은", "저건"), ("저것을", "저걸"), ("그것이", "그게"), ("이것이", "이게"),
    ("무리하지", "무리 말"), ("그러면", "그럼"), ("그렇지만", "하지만"),
    ("어떻게든", "어떻게"), ("아무래도", "아무튼"),
]


def _squeeze(text: str, level: int) -> str:
    """상자에 안 들어갈 때 단계별로 줄인다. 뜻은 절대 건드리지 않는다."""
    if level == 0:
        return text
    if level == 1:                      # 조사 앞뒤가 아닌 곳부터: 두 칸 이상만
        return re.sub(r"  +", " ", text)
    if level == 2:
        # 전각 공백(U+3000)도 같이 지운다. 정규화되면 공백이 되어 줄바꿈 자리를
        # 만드는데, 상자가 빠듯할 때는 그 한 칸 때문에 줄이 하나 더 생긴다.
        return text.replace(" ", "").replace("　", "")
    # 3단계: 말줄임표를 한 글자로. 화면에서 점 개수만 줄고 뜻은 그대로다.
    # (닫는 따옴표 한 글자가 넘쳐 넉 줄이 되던 대사가 여기서 해결된다)
    out = text.replace(" ", "").replace("　", "")
    for a, b in (("……", "…"), ("‥‥", "‥"), ("···", "…"), ("...", "…")):
        out = out.replace(a, b)
    if level == 3:
        return out
    if level >= 5:
        # 5단계: 화자 이름을 뺀다. 한글은 글자당 2바이트라, 일본어 가나로 쓰인
        # 짧은 대사는 이름만으로 원문 예산을 넘긴다(`치즈루「예!」` 12B > 9B).
        # 화면에 초상화가 나오므로 누가 말하는지는 알 수 있다. 마지막 수단이다.
        for _open in ("「", "("):
            k = out.find(_open)
            if 0 < k <= 12:
                out = out[k:]
                break
    # 4단계: 한국어 표준 축약. 뜻과 말투를 유지하면서 글자 수만 줄인다.
    # 엔진이 대사 여러 개를 한 페이지(3줄)에 몰아 담기 때문에, 줄 수가 원문보다
    # 많으면 페이지 경계가 레코드 한가운데로 밀려 다음 대사가 문장 중간부터
    # 나온다. 그래서 원문 줄 수 안에 넣는 것이 화면상 필수다.
    for a, b in CONTRACTIONS:
        out = out.replace(a, b)
    return out


def _assemble(translation_parts, ko_parts, glyph_map, adv, lines, page_break, squeeze,
              strict=True):
    state = LayoutState(glyph_map, max_advance=adv, max_lines=lines,
                        allow_page_break=page_break, strict_width=strict)
    normalisation: list[dict[str, Any]] = []
    original_controls: list[str] = []
    for part in translation_parts:
        kind = part["kind"]
        if kind == "text":
            part_id = part["part_id"]
            if part_id not in ko_parts or not isinstance(ko_parts[part_id], str):
                raise ValueError(f"missing Korean text part {part_id}")
            value, changes = normalise_for_font(_squeeze(ko_parts[part_id], squeeze))
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
