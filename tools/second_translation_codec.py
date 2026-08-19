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
# 대사창 상자에 실제로 들어가는 폭(칸).
# 상자 자체는 32칸이지만 **32칸을 꽉 채우면 게임에서 반 칸이 넘친다**. 그러면
# 렌더러가 자동으로 줄을 접는데, 그때 2바이트 글리프의 선두 바이트를 잃어
# 화자 이름이 통째로 사라지고 문장 중간부터 나온다(2026-08-10 제보, 제3차 9화
# '미치루「왔어 메카자우루스야!잭, 준비됐어?」' = 정확히 32칸).
# 그래서 한 칸 덜 채운다.
MAX_SCENE_ADVANCE = 31
# 대사창 첫 줄은 원점이 한 칸(8px) 왼쪽이라 이론상 두 반칸을 더 쓸 수 있다.
# 그런데 엔진은 연속 대사를 3줄 **쪽** 단위로 이어 그려서, '레코드의 첫 줄'이
# 실제로는 쪽 한가운데인 경우가 많다(제보 #15c 에서 확인). 그때 보너스를 주면
# 바로 그 4px 넘침이 다시 난다. 그래서 지금은 0 으로 둔다 — 모든 줄이
# `2*advance + 줄끝 phase <= 2*상자폭` 을 지킨다.
# (원래 근거였던 실측: 첫 줄 pen native 55 / F6 뒤 63, 잘리는 마지막 픽셀 309)
# (실측: 첫 줄 pen 원점 native 55 / F6 뒤 줄 63, 상자 안쪽 마지막 픽셀 309.
#  레트일 원문도 첫 줄은 반칸 64까지 쓰고 이어지는 줄은 62를 넘지 않는다.)
# 그래서 첫 줄만 반 칸 두 개를 더 쓸 수 있다.
# 전투창·사망창은 원점을 실측한 적이 없고, 레트일도 반칸 58(EX만 59)을 넘지
# 않으므로 보너스를 주지 않는다(0).
SCENE_FIRST_LINE_BONUS_HALF = 0
# 축약 사다리: 0=그대로, 11~19=띄어쓰기를 10~90%만 제거(읽기 좋게 조금씩),
# 2=전부 제거, 3=말줄임표 축약, 4=한국어 축약, 5=화자 이름 생략(최후수단)
SQUEEZE_LADDER = (0, 11, 13, 15, 17, 19, 2, 3, 4, 5)
MAX_PAGE_LINES = 3
# 어떤 낱말도 들어가는 넓이 (마지막 수단)
SAFE_FALLBACK_ADVANCE = 31
# 제어코드가 뒤에 데리고 오는 인자 바이트 수
CONTROL_ARGUMENT_BYTES = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0,
                          0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}


def glyph_advance(index: int, phase: int) -> tuple[int, int]:
    """Return (advance_units, next_phase) for one glyph in the dialogue renderer."""
    if index < 0x101:
        return 1, phase
    return 1 + phase, phase ^ 1


def line_cap_half(max_advance: int, first_line: bool = False,
                  first_line_bonus_half: int = 0) -> int:
    """이 줄이 실제로 쓸 수 있는 **반 칸(4px)** 예산.

    커서는 x = 원점 + 8*advance + 4*phase 로 움직이고, 전각 글리프는 언제나
    12px 를 전진하며 잉크는 그 앞 11px 이다. 따라서 '마지막 글자가 안 잘린다'
    는 조건은 정수 advance 가 아니라

        2*advance + 줄끝 phase  <=  2*상자폭 (+ 첫 줄 보너스)

    이다. 정수만 비교하면 `adv=31 · phase=1`(=반칸 63) 인 줄을 합격시켜
    마지막 전각 글자의 오른쪽 4px 이 잘린다(2026-08-19 제보 #14b).
    """
    return 2 * max_advance + (first_line_bonus_half if first_line else 0)


def widest_half(manifest: dict[str, Any]) -> int:
    """레코드가 상자 오른쪽 끝을 얼마나 밀어냈는지 **반 칸**으로.

    첫 줄 보너스를 이미 뺀 값이라 그냥 ``2 * 상자폭`` 과 비교하면 된다.
    """
    bonus = manifest.get("first_line_bonus_half", 0)
    phases = manifest.get("page_end_phases") or []
    worst = 0
    for i, page in enumerate(manifest["page_advances"]):
        ends = phases[i] if i < len(phases) else []
        for j, adv in enumerate(page):
            ph = ends[j] if j < len(ends) else 0
            worst = max(worst, 2 * adv + ph - (bonus if (i == 0 and j == 0) else 0))
    return worst


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
    # 줄마다 **끝 phase**(0/1). 마지막 전각 글자가 반 칸 더 나갔는지를 이걸로 안다.
    page_end_phases: list[list[int]] = field(default_factory=lambda: [[0]])
    phase: int = 0
    pending_spaces: int = 0
    inserted_line_breaks: int = 0
    inserted_page_breaks: int = 0
    preserved_page_breaks: int = 0
    whitespace_wraps: int = 0
    strict_width: bool = True
    # 이 상자의 **첫 줄**이 더 쓸 수 있는 반 칸 수. 대사창만 2, 나머지는 0.
    first_line_bonus_half: int = 0

    @property
    def current_line(self) -> str:
        return self.pages[-1][-1]

    @property
    def current_cells(self) -> int:
        return self.page_cell_counts[-1][-1]

    @property
    def current_advance(self) -> int:
        return self.page_advances[-1][-1]

    @property
    def cap_half(self) -> int:
        """지금 쓰고 있는 줄의 반칸 예산 (보너스는 레코드 첫 줄에만)."""
        return line_cap_half(self.max_advance,
                             len(self.pages) == 1 and len(self.pages[-1]) == 1,
                             self.first_line_bonus_half)

    def _span(self, text: str, phase: int | None = None) -> tuple[int, int]:
        """(advance, 끝 phase). ``phase`` 를 안 주면 현재 phase 에서 잰다."""
        ph = self.phase if phase is None else phase
        total = 0
        for char in text:
            step, ph = glyph_advance(self.glyph_map[char], ph)
            total += step
        return total, ph

    def _span_advance(self, text: str) -> int:
        """Phase-aware renderer advance of ``text`` starting from the current phase."""
        return self._span(text)[0]

    def _append_visual(self, text: str, cells: int, advance: int = 0) -> None:
        self.pages[-1][-1] += text
        self.page_cell_counts[-1][-1] += cells
        self.page_advances[-1][-1] += advance
        self.page_end_phases[-1][-1] = self.phase

    def _new_line_or_page(self) -> None:
        self.phase = 0  # the renderer resets the wide phase at every F6/F7 break
        if len(self.pages[-1]) < self.max_lines or not self.allow_page_break:
            self.data.append(0xF6)
            self.pages[-1].append("")
            self.page_cell_counts[-1].append(0)
            self.page_advances[-1].append(0)
            self.page_end_phases[-1].append(0)
            self.inserted_line_breaks += 1
        else:
            self.data.append(0xF7)
            self.pages.append([""])
            self.page_cell_counts.append([0])
            self.page_advances.append([0])
            self.page_end_phases.append([0])
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
            # 공백은 저글리프(<0x101)라 한 칸 전진하고 phase 를 바꾸지 않는다
            if 2 * (self.current_advance + 1) + self.phase > self.cap_half:
                self._new_line_or_page()
            self._emit_char(" ")

    def _emit_word(self, word: str) -> None:
        for char in word:
            advance, next_phase = glyph_advance(self.glyph_map[char], self.phase)
            if (self.current_advance
                    and 2 * (self.current_advance + advance) + next_phase
                    > self.cap_half):
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
            span, span_phase = self._span(token)
            # 줄을 새로 시작하면 phase 0 에서 다시 재므로, '이 낱말이 한 줄에
            # 들어가는가'는 phase 0 기준으로 봐야 한다.
            alone, alone_phase = self._span(token, 0)
            fits_alone = 2 * alone + alone_phase <= line_cap_half(self.max_advance)
            if self.pending_spaces:
                # spaces advance one unit each (low glyph, no phase change)
                needed = self.pending_spaces + span
                if (self.current_advance and fits_alone
                        and 2 * (self.current_advance + needed) + span_phase
                        > self.cap_half):
                    # A layout break is the visible separator at a word
                    # boundary; no lexical space is deleted within a line.
                    self._new_line_or_page()
                    self.whitespace_wraps += 1
                else:
                    self._emit_spaces(self.pending_spaces)
                self.pending_spaces = 0
            elif (self.current_advance and fits_alone
                    and 2 * (self.current_advance + span) + span_phase
                    > self.cap_half):
                # There was no legal word boundary.  Split a long token rather
                # than alter or truncate it.
                self._new_line_or_page()
            self._emit_word(token)

    def emit_control(self, raw: bytes, visible_cells: int = 0) -> None:
        if self.pending_spaces:
            if (self.current_advance
                    and 2 * (self.current_advance + self.pending_spaces + visible_cells)
                    + self.phase > self.cap_half):
                self._new_line_or_page()
                self.whitespace_wraps += 1
            else:
                self._emit_spaces(self.pending_spaces)
            self.pending_spaces = 0
        if (visible_cells and self.current_advance
                and 2 * (self.current_advance + visible_cells) + self.phase
                > self.cap_half):
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
        self.page_end_phases.append([0])
        self.phase = 0
        self.preserved_page_breaks += 1

    def finish(self) -> tuple[bytes, dict[str, Any]]:
        # 끝에 남은 공백은 버린다. 화면에는 안 보이면서 줄 폭만 잡아먹어
        # 상자를 한 칸 넘기게 만든다.
        self.pending_spaces = 0
        if self.strict_width and any(
                2 * adv + ph > line_cap_half(self.max_advance, i == 0 and j == 0,
                                             self.first_line_bonus_half)
                for i, (page, ends) in enumerate(
                    zip(self.page_advances, self.page_end_phases))
                for j, (adv, ph) in enumerate(zip(page, ends))):
            raise AssertionError("layout exceeded line-advance (box width) limit")
        if self.allow_page_break and any(len(page) > self.max_lines for page in self.pages):
            raise AssertionError("layout exceeded lines-per-page limit")
        self.data.append(0xFF)
        return bytes(self.data), {
            "pages": self.pages,
            "page_advances": self.page_advances,
            "page_end_phases": self.page_end_phases,
            "first_line_bonus_half": self.first_line_bonus_half,
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
        # 채움 바이트(0x00)도 **글리프라서 폭을 먹는다.** 그냥 뒤에 붙이면 마지막
        # 줄이 상자(32칸)를 넘어 화면이 깨진다. 그래서
        #   1) 줄바꿈(F6/F7) 자리마다 그 줄의 남은 폭만큼 나눠 넣고,
        #   2) 그래도 남으면 쪽에 줄 여유가 있을 때 빈 줄(F6)을 만들어 거기 넣는다.
        out = bytearray(_pad_into_slack(bytes(out), need))
        need = (want - 1) - len(out)
        adv, ln = _tail_line_state(out)
        while need > 0:
            if adv >= MAX_SCENE_ADVANCE and ln < MAX_PAGE_LINES:
                out.append(0xF6); need -= 1; adv = 0; ln += 1
                continue
            out.append(0x00); need -= 1; adv += 1
    out.append(0xFF)
    return bytes(out)


def _pad_into_slack(buf: bytes, need: int) -> bytes:
    """줄마다 남은 폭에 채움 바이트(0x00)를 나눠 넣는다.

    마지막 줄이 이미 꽉 찬 레코드를 뒤에서 채우면 상자를 넘는다. 줄 끝(F6/F7
    직전)마다 그 줄이 상자 폭까지 남긴 만큼만 넣으면 폭이 안 늘어난다.
    """
    if need <= 0:
        return buf
    # 줄 끝 위치와 그 줄의 advance 를 모은다
    ends = []
    adv = phase = 0
    i = 0
    while i < len(buf):
        x = buf[i]
        if x == 0xFF:
            break
        if x < 0xEB:
            idx, i = x, i + 1
        elif x < 0xF6:
            idx, i = ((x - 0xEB) << 8) | buf[i + 1], i + 2
        else:
            if x in (0xF6, 0xF7):
                ends.append((i, adv))
                adv = phase = 0
            i += 1 + CONTROL_ARGUMENT_BYTES.get(x, 0)
            continue
        if idx < 0x101:
            adv += 1
        else:
            adv += 1 + phase
            phase ^= 1
    ends.append((len(buf), adv))            # 마지막 줄
    out = bytearray(buf)
    for pos, a in sorted(ends, key=lambda z: -(MAX_SCENE_ADVANCE - z[1])):
        if need <= 0:
            break
        room = min(need, max(0, MAX_SCENE_ADVANCE - a))
        if room:
            out[pos:pos] = bytes(room)
            need -= room
            ends = [(q + room if q > pos else q, b) for q, b in ends]
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
    first_line_bonus_half: int = 0,
) -> tuple[bytes, dict[str, Any]]:
    adv, lines, page_break = (geometry or (MAX_LINE_ADVANCE, MAX_PAGE_LINES, True))
    bonus = first_line_bonus_half
    if page_break:
        # 원문이 쪽을 나눈 레코드 — 우리도 나눠도 된다.
        narrowest = None
        for squeeze in SQUEEZE_LADDER:
            enc, man = _assemble(translation_parts, ko_parts, glyph_map,
                                 adv, lines, True, squeeze, strict=False,
                                 first_line_bonus_half=bonus)
            widest = widest_half(man)
            if widest <= 2 * adv:
                return enc, man
            if narrowest is None or widest < narrowest[0]:
                narrowest = (widest, enc, man)
        # 어느 단계로도 상자 폭에 못 넣었다 — 낱말 하나가 한 줄보다 긴 경우다.
        # 예전엔 축약 1단계로 되돌렸는데 그게 가장 넓은 후보라 오히려 더 넘쳤다.
        # 가장 좁게 나온 후보를 쓴다.
        if narrowest is not None:
            return narrowest[1], narrowest[2]
        return _assemble(translation_parts, ko_parts, glyph_map,
                         max(adv, SAFE_FALLBACK_ADVANCE), lines, True, 1,
                         first_line_bonus_half=bonus)

    # 원문이 쪽을 안 나눈 레코드 — 대사창 경로는 F7 을 처리하지 못한다.
    # 바이트 상한이 주어지면 그 안에 들어갈 때까지 더 줄인다.
    # 상자(기본 32x3 = 96칸) 안에 넣는다. 안 되면 띄어쓰기를 지운다.
    first = None
    best = None                     # 상자에는 들어가는 것 중 가장 짧은 것
    for squeeze in SQUEEZE_LADDER:
        enc, man = _assemble(translation_parts, ko_parts, glyph_map,
                             adv, lines, False, squeeze, strict=False,
                             first_line_bonus_half=bonus)
        widest = widest_half(man)
        if first is None:
            first = (enc, man)
        fits_box = (widest <= 2 * adv and max(len(pg) for pg in man["pages"]) <= lines)
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
                             adv, lines, False, 5, strict=False,
                             first_line_bonus_half=bonus)
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


def _drop_spaces(text: str, ratio: float) -> str:
    """띄어쓰기를 `ratio` 비율만큼만 지운다 (0.1 = 10%).

    전부 지우면 읽기가 나빠지므로, 한 칸이 모자랄 때는 조금만 지운다.
    지우는 우선순위: 구두점 바로 뒤 → 앞뒤 토막이 짧은 자리 → 나머지.
    """
    pos = [i for i, c in enumerate(text) if c in " 　"]
    if not pos:
        return text
    n = max(1, min(len(pos), round(len(pos) * ratio)))

    def cost(i):
        prev = text[i - 1] if i else ""
        c = 0
        if prev in "!?.,、。」』…‥":
            c -= 10                      # 구두점 뒤 공백은 없어도 잘 읽힌다
        left = i - (text.rfind(" ", 0, i) + 1)
        nxt = text.find(" ", i + 1)
        right = (nxt if nxt >= 0 else len(text)) - i
        return c + min(left, right)

    drop = set(sorted(pos, key=cost)[:n])
    return "".join(c for i, c in enumerate(text) if i not in drop)


def _squeeze(text: str, level: int) -> str:
    """상자에 안 들어갈 때 단계별로 줄인다. 뜻은 절대 건드리지 않는다."""
    if level == 0:
        return text
    if level == 1:                      # 조사 앞뒤가 아닌 곳부터: 두 칸 이상만
        return re.sub(r"  +", " ", text)
    if 10 <= level < 20:
        # 10~19단계: **띄어쓰기를 필요한 만큼만** 지운다. 한 칸이 모자라서 통째로
        # 붙여 쓰던 것을 막는다(EX 대사 295개가 여기 해당했다). 지우는 자리는
        # 눈에 덜 띄는 곳부터 고른다 — 구두점 옆 > 짧은 토막 사이 > 나머지.
        return _drop_spaces(text, (level - 9) / 10.0)
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
              strict=True, first_line_bonus_half=0):
    state = LayoutState(glyph_map, max_advance=adv, max_lines=lines,
                        allow_page_break=page_break, strict_width=strict,
                        first_line_bonus_half=first_line_bonus_half)
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
