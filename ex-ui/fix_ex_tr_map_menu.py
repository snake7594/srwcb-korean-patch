# -*- coding: utf-8 -*-
"""EX / 트레이닝 모드의 맵 명령 메뉴를 제2차·제3차와 같은 전체 이름으로 되돌린다.

레트일 레코드(ui_master 포인터표 table[11], EX 107B / TR 108B)가 짧아
`third-ui/foreign_recs.json` 의 한글은 `부대·반격·목적·정신` 두 글자로 줄여 두었다.
제3차는 그 뒤에 `third-ui/fix_third_ui_leftovers.fix_map_menu()` 가 돌면서 레코드를
폰트 꼬리 도너로 옮기고 전체 이름을 되살리는데, EX·TR 에는 그 단계가 없어서 두
글자짜리가 그대로 화면에 나온다(2026-08-19 제보 #21c).

    다섯 벌 중 EX.WAR·TR.WAR 두 벌만 축약본이었다.
    제2차·SLPS 는 patch_second_exe_ui 의 UI_DISPLAY_COMPACTION + 포인터 재패킹으로,
    제3차는 위 fix_map_menu 로 이미 전체 이름이다.

**모자란 건 칸이 아니라 바이트다.** `부대표`(3전각 4칸 36px)·`반격명령`(4전각 6칸
48px) 은 레트일 `部隊表`·`反撃命令` 과 폭이 정확히 같고, 같은 창의 최장 줄
`페이즈종료`(5전각 7칸 60px)가 이미 들어가 있다. 부족한 것은 레코드 예산으로,
전체 이름은 +14B 인데 레코드 안 여유는 9~10B 뿐이다. 그래서 문안을 줄이는 대신
**폰트 꼬리 도너로 옮기고 table[11] 포인터를 다시 겨눈다** — 나머지 세 벌이 이미
같은 방식으로 옮겨져 실기에서 확인됐다.

옛 자리에는 축약본이 죽은 사본으로 남는다(제3차도 같다). 이 화면을 다시 손볼
때는 반드시 table[11] 을 따라갈 것.
"""
import os as _os
import sys as _sys

_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.exists(_os.path.join(_d, "srwcb_paths.py")):
    _d = _os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import srwcb_paths as _P                                                          # noqa: E402
for _sub in ("tools", "third-ui", "audit"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)

import struct                                                                     # noqa: E402

from second_translation_codec import load_safe_glyph_map, add_extra_glyph_mapping  # noqa: E402
from patch_second_exe_ui import parse_second_ui_vm_record as _PV                   # noqa: E402

PINNED = ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응']
GB, GC, MH_COUNT = 32, 2816, 107
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}

#: 파일 -> (ui_master 헤더, 폰트 오프셋, 레트일 파일)
TARGETS = {
    "EX/EX.WAR": (0x188C4, 0x1D544, "EX/EX.WAR"),
    "TR.WAR": (0x188BC, 0x1D520, "TR.WAR"),
}
EXPAND = (("부대", "부대표"), ("반격", "반격명령"),
          ("목적", "작전목적"), ("정신", "정신검색"))


def _tokens(buf, start, end):
    out, p = [], start
    while p < end:
        x = buf[p]
        if x == 0xFF:
            out.append((p, 1, 'end'))
            break
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


def _arena(war, pre, mh, font_off):
    """도너 후보 — ui_master 가 안 쓰고 폰트 바이트가 레트일 그대로인 글리프 슬롯."""
    used = set()
    for k in range(MH_COUNT):
        f = mh + 4 + 4 * k
        t = f + struct.unpack_from("<i", war, f)[0]
        if not (0x800 <= t < len(war)):
            continue
        for off, ln, kind in _tokens(war, t, _rec_end(war, t)):
            if kind == 'g':
                used.add(_idx(war, off, ln))
    runs = []
    for g in range(0x101, GC):
        a = font_off + g * GB
        if g in used or war[a:a + GB] != pre[a:a + GB]:
            continue
        if runs and runs[-1][1] == a:
            runs[-1][1] = a + GB
        else:
            runs.append([a, a + GB])
    runs.sort(key=lambda r: r[0], reverse=True)      # 폰트 꼬리부터 쓴다
    return runs


def fix_one(war_bytes: bytes, pre: bytes, mh: int, font_off: int) -> tuple:
    war = bytearray(war_bytes)
    ko = add_extra_glyph_mapping(load_safe_glyph_map(), PINNED)
    tag = b"".join(_enc_idx(ko[c]) for c in "페이즈")
    slot = s = e = None
    for k in range(MH_COUNT):
        f = mh + 4 + 4 * k
        t0 = f + struct.unpack_from("<i", war, f)[0]
        if not (0x800 <= t0 < len(war)):
            continue
        try:
            e0, _tk = _PV(bytes(war), t0)
        except Exception:
            continue
        if tag in bytes(war[t0:e0]):
            slot, s, e = f, t0, e0
            break
    if slot is None:
        return bytes(war), "메뉴를 찾지 못함"
    old = bytes(war[s:e])
    new = old
    for short, full in EXPAND:
        a = b"".join(_enc_idx(ko[c]) for c in short)
        b = b"".join(_enc_idx(ko[c]) for c in full)
        if b in new:                       # 이미 고쳐져 있으면 그대로 둔다(멱등)
            continue
        br = bytes([0xF6])
        if br + a + br not in new:
            return bytes(war), f"항목 '{short}' 을 찾지 못함"
        new = new.replace(br + a + br, br + b + br, 1)
        # 새로 쓰는 글자의 폰트 자리가 도너로 회수돼 있으면 글자가 깨진다
        for ch in full[len(short):]:
            g = ko[ch]
            o = font_off + g * GB
            if war[o:o + GB] == pre[o:o + GB]:
                return bytes(war), f"'{ch}' 글리프({g})가 한글이 아님 — 중단"
    if new == old:
        return bytes(war), "이미 전체 이름"
    if len(new) <= len(old):
        war[s:s + len(new)] = new
        return bytes(war), f"제자리 ({len(new)}/{len(old)}B)"
    for a, b in _arena(bytes(war), pre, mh, font_off):
        if b - a >= len(new):
            war[a:a + len(new)] = new
            struct.pack_into("<i", war, slot, a - slot)
            return bytes(war), f"도너 @{a:#x} ({len(new)}B, 원래 {len(old)}B)"
    return bytes(war), "도너 공간 부족"


def apply(files: dict) -> None:
    for key, (mh, font_off, retail) in TARGETS.items():
        if key not in files:
            continue
        pre = (_P.EXTRACTED / retail).read_bytes()
        out, msg = fix_one(files[key], pre, mh, font_off)
        if len(out) != len(files[key]):
            raise SystemExit(f"{key} 크기가 변했습니다")
        files[key] = out
        print(f"  {key} 맵 명령 메뉴: {msg}")
