# -*- coding: utf-8 -*-
"""빌드 끝에 한 번 더 훑어 **아직 일본어로 남은 UI 레코드**를 보충한다.

전면 재검증(v0.10.8)에서 나온 결론을 빌드에 붙박이로 넣은 것이다. 그때는 완성된
이미지를 뜯어 고쳤는데, 그러면 다시 빌드할 때마다 같은 문제가 되살아난다.

찾는 법 — 실행파일을 통째로 훑으면 MIPS 명령이 가나로 디코드돼 오탐이 수천 건
난다. 그래서 **포인터가 실제로 가리키는 레코드만** 본다.
  * 테이블 포인터 (지형/정신기/무기/파일럿/유닛 이름 등)
  * ui_master 107개
  * 필드상대 포인터 (target = field + s32(field)) — 사전·작품명·BGM 제목류

고치는 법 — 트레이닝 모드는 EX 의 쌍둥이라 같은 문자열이 EX 에 번역·폭검증된 채로
있다. 레트일 바이트가 일치하는 EX 위치를 찾아 EX 패치본 바이트를 그대로 가져온다
(제자리 번역이라 길이가 같다). 이걸로 트레이닝 모드 상시 UI 대부분이 해결된다.
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
for _sub in ("tools", "audit"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import struct
import sys

sys.path.insert(0, str(_P.TOOLS))
import audit_all as A  # noqa: E402
from patch_second_exe_ui import parse_second_ui_vm_record as PV  # noqa: E402

# 게임별 (ui_master 헤더, 테이블들, extras)
LAYOUT = {
    "EX.WAR": dict(MH=0x188C4, extras=A.EX15,
                   tables=[(0xbcb4, 144), (0xc184, 94), (0xc850, 64), (0xcbcc, 1344),
                           (0xf258, 52), (0xf510, 22), (0xf61c, 192), (0x10778c, 400),
                           (0x1081bc, 400), (0x108f64, 448)]),
    "TR.WAR": dict(MH=0x188BC, extras=A.EX15,
                   tables=[(0xbcac, 144), (0xc17c, 94), (0xc848, 64), (0xcbc4, 1344),
                           (0xf250, 52), (0xf508, 22), (0xf614, 192), (0x107768, 400),
                           (0x108198, 400), (0x108f40, 448)]),
}


def pointed_records(war: bytes, name: str) -> set:
    """포인터가 가리키는 레코드 시작 오프셋."""
    cfg = LAYOUT[name]
    out = set()
    for ptr, cnt in cfg["tables"]:
        for k in range(cnt):
            f = ptr + 4 + 4 * k
            t = f + struct.unpack_from("<i", war, f)[0]
            if 0x800 <= t < len(war):
                out.add(t)
    MH = cfg["MH"]
    for k in range(107):
        f = MH + 4 + 4 * k
        t = f + struct.unpack_from("<i", war, f)[0]
        if 0x800 <= t < len(war):
            out.add(t)
    return out


def field_pointed(war: bytes, lo=0x800, hi=None) -> set:
    """필드상대 포인터가 가리키는 곳. 4바이트 정렬이 아니라 바이트마다 본다."""
    hi = hi or len(war)
    out = set()
    for f in range(lo, min(hi, len(war) - 4)):
        d = struct.unpack_from("<i", war, f)[0]
        t = f + d
        if 0x800 <= t < len(war) and abs(d) < 0x40000:
            out.add(t)
    return out


def untranslated(war: bytes, name: str) -> list:
    """아직 일본어인 레코드 [{off, end, jp}]."""
    tbl = A.ko_table(LAYOUT[name]["extras"])
    hits = []
    for t in sorted(pointed_records(war, name)):
        e = A.rec_end(war, t)
        if e <= t or e - t < 3:
            continue
        try:
            s = A.decode(war, t, e, tbl)
        except Exception:
            continue
        if not s:
            continue
        n, tot = A.jp_ratio(s)
        if n >= 2 and tot and n / tot >= 0.5:
            hits.append({"off": t, "end": e, "jp": s})
    return hits


TR_FONT_OFF, GB, GC = 0x1d520, 32, 2816


def _glyphs(buf, s, e):
    out, p = [], s
    while p < e - 1:
        x = buf[p]
        if x < 0xEB:
            out.append(x); p += 1
        elif x <= 0xF5:
            out.append(((x - 0xEB) << 8) | buf[p + 1]); p += 2
        else:
            p += 1 + A.ARG.get(x, 0)
    return out


def _reclaimed_slots(tr_war: bytes) -> set:
    """도너로 회수돼 이제 글자가 아닌 슬롯 (주입 전 폰트와 다른 자리)."""
    pre = (_P.WORK / "test_build" / "ex_full" / "font_extracted" / "TR.WAR").read_bytes()
    out = set()
    for g in range(0x101, GC):
        a = TR_FONT_OFF + g * GB
        if tr_war[a:a + GB] != pre[a:a + GB]:
            out.add(g)
    return out


def apply(files: dict) -> int:
    """files 의 TR.WAR 을 EX.WAR 에서 보충한다. 반환: 고친 개수."""
    import fix_tr_leftover as FT
    tr_war = files["TR.WAR"]
    ex_war = files["EX/EX.WAR"]
    tr_ret = (_P.EXTRACTED / "TR.WAR").read_bytes()
    ex_ret = (_P.EXTRACTED / "EX" / "EX.WAR").read_bytes()

    items = untranslated(tr_war, "TR.WAR")
    # 필드상대 포인터가 가리키는 것도 포함 (사전·작품명·BGM 제목류)
    tbl = A.ko_table(A.EX15)
    seen = {x["off"] for x in items}
    for t in sorted(field_pointed(tr_war)):
        if t in seen:
            continue
        e = A.rec_end(tr_war, t)
        if e <= t or e - t < 6 or e - t > 80:
            continue
        try:
            s = A.decode(tr_war, t, e, tbl)
        except Exception:
            continue
        n, tot = A.jp_ratio(s)
        if s and n >= 3 and tot and n / tot >= 0.6:
            items.append({"off": t, "end": e, "jp": s})
            seen.add(t)

    fixed, n, det = FT.port(tr_war, tr_ret, ex_war, ex_ret, items, verbose=False)
    if len(fixed) != len(tr_war):
        raise SystemExit("TR.WAR 크기가 변했습니다")
    # EX 의 한글이 TR 이 이미 **도너로 회수한 글리프 슬롯**을 쓰면 그 글자는 깨진다.
    # (EX 와 TR 은 회수한 슬롯이 서로 다르다.) 그런 이식은 되돌린다.
    dead = _reclaimed_slots(tr_war)
    undo = 0
    out = bytearray(fixed)
    for x in items:
        t, e = x["off"], x["end"]
        if bytes(out[t:e]) == bytes(tr_war[t:e]):
            continue
        if any(g in dead for g in _glyphs(out, t, e)):
            out[t:e] = tr_war[t:e]
            undo += 1
    if undo:
        print(f"    도너 충돌로 되돌림 {undo}건")
        n -= undo
    files["TR.WAR"] = bytes(out)
    print(f"    후보 {len(items)}건 검사 -> EX 에서 {n}건 이식")
    for t, jp, ko in det[:8]:
        print(f"      {t:#07x} '{jp}' -> '{ko}'")
    n += repair_dead_glyphs(files)
    return n


def repair_dead_glyphs(files: dict) -> int:
    """회수된(도너로 넘어간) 글리프 슬롯을 참조하는 살아있는 TR 레코드를 고친다.

    TR 과 EX 는 회수한 슬롯이 서로 다르다. 어느 단계에서 EX 쪽 바이트가 그대로
    들어오면 TR 에서는 그 자리가 이미 글자가 아니라서 화면에 깨져 나온다.
    같은 테이블 자리의 EX 텍스트를 읽어 **TR 의 글리프로 다시 인코딩**한다.
    """
    from second_translation_codec import (load_safe_glyph_map, add_extra_glyph_mapping,
                                          normalise_for_font)
    war = bytearray(files["TR.WAR"])
    ex = files["EX/EX.WAR"]
    dead = _reclaimed_slots(bytes(war))
    ex_dead = _reclaimed_slots_ex(ex)
    gm = add_extra_glyph_mapping(load_safe_glyph_map(), A.EX15)
    tbl = A.ko_table(A.EX15)
    pairs = list(zip(LAYOUT["TR.WAR"]["tables"], LAYOUT["EX.WAR"]["tables"]))
    fixed = 0
    for (tp, cnt), (ep, _c) in pairs:
        for k in range(cnt):
            f = tp + 4 + 4 * k
            t = f + struct.unpack_from("<i", war, f)[0]
            if not (0x800 <= t < len(war)):
                continue
            e = A.rec_end(war, t)
            if e <= t or not (dead & set(_glyphs(war, t, e))):
                continue
            g = ep + 4 + 4 * k
            s = g + struct.unpack_from("<i", ex, g)[0]
            if not (0x800 <= s < len(ex)):
                continue
            se = A.rec_end(ex, s)
            if se <= s or (ex_dead & set(_glyphs(ex, s, se))):
                continue
            txt = A.decode(ex, s, se, tbl)
            enc = bytearray()
            ok = True
            for ch in normalise_for_font(txt)[0]:
                i = gm.get(ch)
                if i is None or i in dead:
                    ok = False
                    break
                enc += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
            enc += b"\xFF"
            if not ok or len(enc) > e - t:
                continue
            war[t:t + len(enc)] = enc
            war[t + len(enc):e] = b"\x00" * (e - t - len(enc))
            fixed += 1
            print(f"      깨진 글리프 복구 {t:#07x} -> {txt!r}")
    files["TR.WAR"] = bytes(war)
    return fixed


def _reclaimed_slots_ex(ex_war: bytes) -> set:
    pre = (_P.WORK / "test_build" / "ex_full" / "font_extracted" / "EX" / "EX.WAR").read_bytes()
    off = 0x1d544
    return {g for g in range(0x101, GC)
            if ex_war[off + g * GB:off + (g + 1) * GB] != pre[off + g * GB:off + (g + 1) * GB]}


if __name__ == "__main__":
    import json
    war = (_P.WORK / "test_build" / "tr_full" / "TR_final.war").read_bytes()
    items = untranslated(war, "TR.WAR")
    print(f"TR.WAR 미번역 후보 {len(items)}건")
    for x in items[:20]:
        print(f"  {x['off']:#07x} {x['jp'][:40]}")
