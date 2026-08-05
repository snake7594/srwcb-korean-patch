# -*- coding: utf-8 -*-
"""전면 재검증에서 찾은 잔여 미번역 레코드를 번역해 도너로 재배치한다.

대상: 옵션 모드의 작품명·BGM/데모 제목, 트레이닝 모드 UI 문자열 등 — 번역은
있는데(사전 보유) 한글이 원문보다 길어 제자리에 못 넣어 남아 있던 것들.

방식(검증된 relocate_pointed_records 와 동일):
  1) 레코드를 가리키는 필드상대 포인터를 찾는다. **정확히 1개일 때만** 손댄다.
  2) 한글을 인코딩해 폰트 도너(쓰지 않는 글리프 슬롯)에 쓰고 포인터를 재조준한다.
  3) 원본 자리는 그대로 둔다(죽은 사본, 무해).

도너 안전성: 살아있는 레코드가 참조하는 글리프 슬롯은 절대 쓰지 않는다.
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
for _sub in ("tools", "third-ui", "ex-ui", "tr-ui", "audit", "menu-align", "second-fixes"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import sys, json, struct, os
from pathlib import Path

R = str(_P.WORK)
sys.path.insert(0, f"{R}/tools")
sys.path.insert(0, str(Path(__file__).parent))
import audit_all as A
from second_translation_codec import (load_safe_glyph_map, add_extra_glyph_mapping,
                                      normalise_for_font)
from patch_second_exe_ui import parse_second_ui_vm_record as PV

GB, GC = 32, 2816
CB = A.CB

CFG = {
    "SECOND.WAR": dict(iso="SECOND/SECOND.WAR", retail=f"{R}/extracted/SECOND/SECOND.WAR",
                       extras=A.PINNED, font=0x28058, MH=0x24320,
                       dyn=f"{R}/test_build/second_korean_v0.9.2/font/srwcb_font_hangul_dynamic_2816_16x16.bin"),
    "THIRD.WAR": dict(iso="THIRD/THIRD.WAR", retail=f"{R}/extracted/THIRD/THIRD.WAR",
                      extras=A.PINNED, font=0x2872c, MH=0x247CC,
                      dyn=f"{R}/test_build/third_full/font/srwcb_font_hangul_dynamic_2816_16x16.bin"),
    "EX.WAR": dict(iso="EX/EX.WAR", retail=f"{R}/extracted/EX/EX.WAR",
                   extras=A.EX15, font=0x1d544, MH=0x188C4,
                   dyn=f"{R}/test_build/ex_full/font/srwcb_font_hangul_dynamic_2816_16x16.bin"),
    "TR.WAR": dict(iso="TR.WAR", retail=f"{R}/extracted/TR.WAR",
                   extras=A.EX15, font=0x1d520, MH=0x188BC,
                   dyn=f"{R}/test_build/ex_full/font/srwcb_font_hangul_dynamic_2816_16x16.bin"),
}
# 테이블 헤더(살아있는 글리프 수집용)
TABLES = {
    "SECOND.WAR": [], "THIRD.WAR": [(0xbb0c, 144), (0xbf68, 94), (0xc634, 64), (0xc9ac, 1408),
                                    (0x1130c, 52), (0x1155c, 22), (0x11668, 192), (0x10dbf8, 400),
                                    (0x10eb2c, 400), (0x110208, 448)],
    "EX.WAR": [(0xbcb4, 144), (0xc184, 94), (0xc850, 64), (0xcbcc, 1344), (0xf258, 52),
               (0xf510, 22), (0xf61c, 192), (0x10778c, 400), (0x1081bc, 400), (0x108f64, 448)],
    "TR.WAR": [(0xbcac, 144), (0xc17c, 94), (0xc848, 64), (0xcbc4, 1344), (0xf250, 52),
               (0xf508, 22), (0xf614, 192), (0x107768, 400), (0x108198, 400), (0x108f40, 448)],
}


class Fixer:
    def __init__(self, name, war=None):
        self.name = name
        self.cfg = CFG[name]
        self.war = bytearray(war if war is not None else A.read_iso(CB, self.cfg["iso"]))
        self.ret = open(self.cfg["retail"], "rb").read()
        self.gm = add_extra_glyph_mapping(load_safe_glyph_map(), self.cfg["extras"])
        self.writes = []
        self._arena()

    def _live_glyphs(self):
        used = set()
        for ptr, cnt in TABLES.get(self.name, []):
            for k in range(cnt):
                f = ptr + 4 + 4 * k
                t = f + struct.unpack_from("<i", self.war, f)[0]
                if 0x800 <= t < len(self.war):
                    for off, n, kk, g in A.toks(self.war, t, A.rec_end(self.war, t)):
                        if kk == 'g':
                            used.add(g)
        MH = self.cfg["MH"]
        for k in range(107):
            f = MH + 4 + 4 * k
            t = f + struct.unpack_from("<i", self.war, f)[0]
            if not (0x800 <= t < len(self.war)):
                continue
            try:
                _e, tk = PV(bytes(self.war), t)
            except Exception:
                continue
            for x in tk:
                if x.kind == 'glyph':
                    r = x.raw
                    used.add(r[0] if len(r) == 1 else ((r[0] - 0xEB) << 8) | r[1])
        return used

    def _arena(self):
        """폰트에서 '아직 원본 그대로이고 아무도 안 쓰는' 글리프 슬롯 = 도너."""
        dyn = open(self.cfg["dyn"], "rb").read()
        foff = self.cfg["font"]
        live = self._live_glyphs()
        extras_end = 0xA2F + len(self.cfg["extras"])
        free = []
        for g in range(extras_end, GC):
            if g in live:
                continue
            a = foff + g * GB
            if bytes(self.war[a:a + GB]) == dyn[g * GB:(g + 1) * GB]:
                free.append([a, a + GB])
        merged = []
        for a, b in free:
            if merged and merged[-1][1] == a:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        self.pool = merged
        self.free_total = sum(b - a for a, b in merged)

    def alloc(self, n):
        for blk in self.pool:
            if blk[1] - blk[0] >= n:
                p = blk[0]; blk[0] += n
                return p
        return None

    def enc(self, s):
        o = bytearray()
        for ch in normalise_for_font(s)[0]:
            i = self.gm.get(ch)
            if i is None:
                return None
            o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
        return bytes(o)

    def fields_to(self, t, span=0x20000):
        """레코드를 가리키는 필드상대 포인터 (패치본 기준)."""
        out = []
        lo = max(0, t - span); hi = min(len(self.war) - 4, t + 0x200)
        for f in range(lo, hi):
            if f + struct.unpack_from("<i", self.war, f)[0] == t:
                out.append(f)
        return out

    def fix(self, off, ko):
        """반환: 'ok' | 사유"""
        e = A.rec_end(self.ret, off)
        if e <= off:
            return "레코드아님"
        if bytes(self.war[off:e]) != self.ret[off:e]:
            return "이미변경"
        kb = self.enc(ko)
        if kb is None:
            return "글리프없음"
        body = kb + b"\xFF"
        if len(body) <= e - off:            # 제자리 가능
            self.war[off:off + len(body)] = body
            self.war[off + len(body):e] = b"\x00" * (e - off - len(body))
            self.writes.append((off, e))
            return "제자리"
        fs = self.fields_to(off)
        if len(fs) != 1:
            return f"포인터{len(fs)}개"
        pos = self.alloc(len(body))
        if pos is None:
            return "도너부족"
        self.war[pos:pos + len(body)] = body
        struct.pack_into("<i", self.war, fs[0], pos - fs[0])
        self.writes.append((pos, pos + len(body)))
        self.writes.append((fs[0], fs[0] + 4))
        return "도너"
