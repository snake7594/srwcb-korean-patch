# -*- coding: utf-8 -*-
"""제3차 실행파일에 남아 있던 UI 잔재를 고친다 (제보 #5).

세 가지다.

  1. **유닛 상태창 '타입' 값** — 지형 조합표(0x944f~0x947a)가 통째로 미번역이라
     일본어 글리프 번호가 그대로 남았다. 그 번호는 한글로 덮인 자리라 화면에는
     엉뚱한 글자가 겹쳐 나온다. 한자와 한글 모두 2바이트라 **제자리 교체**로 끝난다.
  2. **출격 유닛 선택 헤더** — '남은 ○機' 의 `機` 가 같은 이유로 깨져 나온다.
  3. (보류) **맵 명령 메뉴** — 레트일 레코드가 짧아 `부대표/반격명령/작전목적/
     정신검색` 이 두 글자로 잘려 있다. 도너로 옮기려면 UI-VM 레코드를 옵코드까지
     이해해 다시 써야 해서 아직 손대지 않았다.
"""

# --- 이식용 부트스트랩 (자동 삽입): 저장소 어디서 실행하든 동작 ---
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.exists(_os.path.join(_d, "srwcb_paths.py")):
    _d = _os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import srwcb_paths as _P
_P.ensure_dirs()
for _sub in ("tools", "third-ui", "audit", "menu-align"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import json
import struct
import sys

sys.path.insert(0, str(_P.TOOLS))
from second_translation_codec import (load_safe_glyph_map, add_extra_glyph_mapping)  # noqa: E402

PINNED = ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응']
FONT_OFF, GB, GC = 0x2872c, 32, 2816
MH, MH_COUNT = 0x247CC, 107
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}

# 지형/단위 한자 -> 한글. 한자·한글 모두 2바이트라 길이가 안 변한다.
KANJI = {"陸": "육", "空": "공", "水": "수", "海": "해", "宇": "우", "機": "기"}

# 맵 명령 메뉴 (제2차와 같은 표기로 되돌린다)
MAP_MENU = ["페이즈종료", "부대표", "반격명령", "작전목적", "정신검색", "시스템", "저장"]


def _tokens(buf, start, end):
    out, p = [], start
    while p < end:
        x = buf[p]
        if x == 0xFF:
            out.append((p, 1, 'end')); break
        if x < 0xEB:
            out.append((p, 1, 'g')); p += 1
        elif x <= 0xF5:
            out.append((p, 2, 'g')); p += 2
        else:
            n = 1 + CTRL.get(x, 0)
            out.append((p, n, 'c')); p += n
    return out


def _rec_end(buf, s):
    for off, n, kind in _tokens(buf, s, len(buf)):
        if kind == 'end':
            return off + 1
    return s


def _idx(buf, o, n):
    return buf[o] if n == 1 else ((buf[o] - 0xEB) << 8) | buf[o + 1]


def _enc_idx(i):
    return bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))


def substitute_kanji(war: bytearray, jp_idx2ch: dict, ko_ch2idx: dict,
                     lo=0x944f, hi=0x94d7) -> int:
    """UI 영역에 남은 일본어 한자를 같은 크기의 한글로 제자리 교체."""
    want = {i: KANJI[c] for i, c in jp_idx2ch.items() if c in KANJI}
    # 두음법칙: 이미 번역된 칸이 '륙' 으로 들어가 있어 표기가 섞여 있다
    want.update({ko_ch2idx["륙"]: "육"})
    n = 0
    p = lo
    while p < hi:
        e = _rec_end(war, p)
        if e <= p:
            p += 1
            continue
        for off, ln, kind in _tokens(war, p, e):
            if kind == 'g' and ln == 2:
                g = _idx(war, off, 2)
                if g in want:
                    war[off:off + 2] = _enc_idx(ko_ch2idx[want[g]])
                    n += 1
        p = e
    return n


def substitute_kanji_ui(war: bytearray, jp_idx2ch: dict, ko_ch2idx: dict) -> int:
    """ui_master 레코드 안의 한자 잔재. UI-VM 문법으로 토큰을 떠서 글리프만 바꾼다
    (대사 문법으로 읽으면 0xF0~0xF5 옵코드를 글리프 선두로 오독한다)."""
    from patch_second_exe_ui import parse_second_ui_vm_record as PV
    want = {i: KANJI[c] for i, c in jp_idx2ch.items() if c in KANJI}
    n = 0
    for k in range(MH_COUNT):
        f = MH + 4 + 4 * k
        t = f + struct.unpack_from("<i", war, f)[0]
        if not (0x800 <= t < len(war)):
            continue
        try:
            _e, toks = PV(bytes(war), t)
        except Exception:
            continue
        wide = [i for i, x in enumerate(toks)
                if x.kind == 'glyph' and len(x.raw) == 2]
        for pos, i in enumerate(wide):
            x = toks[i]
            g = ((x.raw[0] - 0xEB) << 8) | x.raw[1]
            if g not in want:
                continue
            # 한글 폰트에서 이 번호들은 이미 한글 글자다(機=481 -> '꼽' 등).
            # 번역된 단어 한가운데를 바꾸면 멀쩡한 한글이 깨진다. 앞뒤가 모두
            # 전각이 아닌 **외톨이 글자**일 때만 미번역 잔재로 보고 바꾼다.
            prev_adj = pos and wide[pos - 1] == i - 1
            next_adj = pos + 1 < len(wide) and wide[pos + 1] == i + 1
            if prev_adj or next_adj:
                continue
            war[x.start:x.start + 2] = _enc_idx(ko_ch2idx[want[g]])
            n += 1
    return n


def _arena(war: bytes, pre: bytes):
    """폰트에서 아직 원본 그대로이고 아무도 안 쓰는 글리프 슬롯 = 도너."""
    used = set()
    for k in range(MH_COUNT):
        f = MH + 4 + 4 * k
        t = f + struct.unpack_from("<i", war, f)[0]
        if not (0x800 <= t < len(war)):
            continue
        for off, ln, kind in _tokens(war, t, _rec_end(war, t)):
            if kind == 'g':
                used.add(_idx(war, off, ln))
    free = []
    for g in range(0x101, GC):
        a = FONT_OFF + g * GB
        if g in used or war[a:a + GB] != pre[a:a + GB]:
            continue
        free.append(a)
    # 연속 구간으로 묶는다
    runs = []
    for a in free:
        if runs and runs[-1][1] == a:
            runs[-1][1] = a + GB
        else:
            runs.append([a, a + GB])
    # 폰트 꼬리(번호가 가장 큰 글리프)부터 쓴다. 한글은 0x101~0xA2E 에만 있어
    # 꼬리 쪽은 아무도 안 그린다 — 앞쪽 빈칸은 남은 일본어가 쓸 수도 있다.
    runs.sort(key=lambda r: r[0], reverse=True)
    return runs


def fix_map_menu(war: bytearray, pre: bytes, ko_ch2idx: dict) -> str:
    """맵 명령 메뉴를 제2차와 같은 전체 이름으로 되돌린다.

    레트일 레코드가 짧아 `부대/반격/목적/정신` 두 글자로 잘려 있었다. 늘리면
    14바이트가 더 필요한데 레코드 안 여유는 9바이트뿐이라 **도너 자리로 옮기고
    포인터를 다시 겨눈다**. 레코드는 UI-VM 문법이라(0xF0~0xF5 가 옵코드다)
    끝을 찾을 때 대사 문법으로 자르면 안 된다 — PV 로 뜬다.
    """
    from patch_second_exe_ui import parse_second_ui_vm_record as PV
    tag = b"".join(_enc_idx(ko_ch2idx[c]) for c in "페이즈")
    slot = s = e = None
    for k in range(MH_COUNT):
        f = MH + 4 + 4 * k
        t0 = f + struct.unpack_from("<i", war, f)[0]
        if not (0x800 <= t0 < len(war)):
            continue
        try:
            e0, _tk = PV(bytes(war), t0)
        except Exception:
            continue
        if tag in bytes(war[t0:e0]):
            slot, s, e = f, t0, e0
            break
    if slot is None:
        return "메뉴를 찾지 못함"
    old = bytes(war[s:e])
    new = old
    for short, full in (("부대", "부대표"), ("반격", "반격명령"),
                        ("목적", "작전목적"), ("정신", "정신검색")):
        a = b"".join(_enc_idx(ko_ch2idx[c]) for c in short)
        b = b"".join(_enc_idx(ko_ch2idx[c]) for c in full)
        # 줄 구분자(F6) 사이에 홀로 있는 항목만 바꾼다
        br = bytes([0xF6])
        if br + a + br not in new:
            return f"항목 '{short}' 을 찾지 못함"
        new = new.replace(br + a + br, br + b + br, 1)
    if len(new) <= len(old):
        war[s:s + len(new)] = new
        return f"제자리 ({len(new)}/{len(old)}B)"
    for a, b in _arena(bytes(war), pre):
        if b - a >= len(new):
            war[a:a + len(new)] = new
            struct.pack_into("<i", war, slot, a - slot)
            return f"도너 @{a:#x} ({len(new)}B, 원래 {len(old)}B)"
    return "도너 공간 부족"


def apply(war_bytes: bytes) -> tuple:
    war = bytearray(war_bytes)
    pre = (_P.EXTRACTED / "THIRD" / "THIRD.WAR").read_bytes()
    rows = json.loads(_P.FONT_MAPPING.read_text(encoding="utf-8"))["rows"]
    jp_idx2ch = {r["glyph_index"]: (r.get("character") or "") for r in rows}
    ko = add_extra_glyph_mapping(load_safe_glyph_map(), PINNED)
    n = substitute_kanji(war, jp_idx2ch, ko)
    n += substitute_kanji_ui(war, jp_idx2ch, ko)
    menu = fix_map_menu(war, pre, ko)
    if len(war) != len(war_bytes):
        raise SystemExit("THIRD.WAR 크기가 변했습니다")
    return bytes(war), n, menu


if __name__ == "__main__":
    src = _P.WORK / "test_build" / "third_full" / "runtime" / "THIRD" / "THIRD.WAR"
    out, n, menu = apply(src.read_bytes())
    print(f"한자 잔재 제자리 교체 {n}곳 / 맵 명령 메뉴: {menu}")
