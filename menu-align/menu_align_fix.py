# -*- coding: utf-8 -*-
"""제3차/EX/TR 출하본(v0.10.5)의 ui_master 메뉴 정렬을 제2차 기준으로 교정한다.

원칙 (제2차 전수 감사에서 확인된 검증 규칙):
  * 모든 텍스트 런의 renderer advance 를 레트일과 정확히 일치시킨다.
    부족분은 반각 0x00 을 '뒤'에 채우고, 레트일의 선행 공백(들여쓰기)은 보존한다.
  * 번역이 길면 축약보다 띄어쓰기 제거를 먼저 시도한다(사용자 지시).
  * 제2차와 레트일 바이트가 같은 레코드는 제2차 패치본 레코드를 통째 이식한다
    (제2차의 앵커 인자 보정 FC FC 02→FD 까지 함께 온다). 아니면 런 단위로
    제2차의 같은 원문 번역(pairs)을 우선 적용한다.
  * 컨트롤은 항상 '레트일 원본 + 제2차가 한 보정 2건' 만 쓴다. 과거 주입기가
    폭 안 맞는 런을 앵커 조작으로 땜질한 것(제3차 ui[39])은 폐기된다.
  * 레트일이 참조하는 고슬롯 합자 글리프(0xA39+, 誕生日 등 한 칸 압축 한자)가
    extras/도너에 덮인 게임(EX/TR)은 합자 비트맵을 빈 슬롯에 복원하고 참조를
    리매핑한다. (지금까지 그 화면들은 '맀뿠삤' 같은 깨진 글자가 나갔다.)

빌드 재현이 아니라 출하본 바이트를 직접 고친다: 교정 레코드는 전부 도너(폰트
미사용 슬롯)에 새로 쓰고 포인터만 재조준하므로 다른 영역은 1바이트도 안 바뀐다.
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
import json, math, os, struct, sys
from pathlib import Path

SP = str(_P.BUILD)   # 중간 산출물(캐시·교정본)을 두는 곳
ROOT = str(_P.WORK)
sys.path.insert(0, str(_P.TOOLS)); sys.path.insert(0, SP)
from patch_second_exe_ui import parse_second_ui_vm_record as PV
from second_translation_codec import (load_safe_glyph_map, add_extra_glyph_mapping,
                                      normalise_for_font)
import second_ui_transplant as ST

IMG = (f"{ROOT}/test_build/third_full/"
       "Super Robot Taisen Complete Box Korean v0.10.5 (Track 1).bin")
SEC, UDO, UDS = 2352, 24, 2048
GB, GC = 32, 2816
PINNED = ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응']
EX15 = PINNED + ['맀', '뿠', '삤', '읏', '햣']
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}

GAMES = {
    "THIRD": dict(
        retail=f"{ROOT}/extracted/THIRD/THIRD.WAR", iso="THIRD.WAR",
        MH=0x247CC, font=0x2872C, extras=PINNED,
        dyn=f"{ROOT}/test_build/third_full/font/srwcb_font_hangul_dynamic_2816_16x16.bin",
        tables=[(0xbb0c, 144), (0xbf68, 94), (0xc634, 64), (0xc9ac, 1408),
                (0x1130c, 52), (0x1155c, 22), (0x11668, 192), (0x10dbf8, 400),
                (0x10eb2c, 400), (0x110208, 448)]),
    "EX": dict(
        retail=f"{ROOT}/extracted/EX/EX.WAR", iso="EX.WAR",
        MH=0x188C4, font=0x1D544, extras=EX15,
        dyn=f"{ROOT}/test_build/ex_full/font/srwcb_font_hangul_dynamic_2816_16x16.bin",
        tables=[(0xbcb4, 144), (0xc184, 94), (0xc850, 64), (0xcbcc, 1344),
                (0xf258, 52), (0xf510, 22), (0xf61c, 192), (0x10778c, 400),
                (0x1081bc, 400), (0x108f64, 448)]),
    "TR": dict(
        retail=f"{ROOT}/extracted/TR.WAR", iso="TR.WAR",
        MH=0x188BC, font=0x1D520, extras=EX15,
        dyn=f"{ROOT}/test_build/ex_full/font/srwcb_font_hangul_dynamic_2816_16x16.bin",
        tables=[(0xbcac, 144), (0xc17c, 94), (0xc848, 64), (0xcbc4, 1344),
                (0xf250, 52), (0xf508, 22), (0xf614, 192), (0x107768, 400),
                (0x108198, 400), (0x108f40, 448)]),
}

_mpj = json.load(open(f"{ROOT}/research/srwcb_embedded_font_mapping_reviewed.json",
                      encoding="utf-8"))
I2C = {r["glyph_index"]: (r.get("character") or "") for r in _mpj["rows"]}


# 빌드 파이프라인이 넘겨주는 실행파일 버퍼 (이미지 대신 쓴다).
SOURCES: dict[str, bytes] = {}


def rd_iso(name, size_holder={}):
    if name in SOURCES:
        return bytearray(SOURCES[name])
    from extract_psx_iso import RawMode2Image, read_tree
    if "E" not in size_holder:
        with RawMode2Image(Path(IMG)) as m:
            _, E = read_tree(m)
        size_holder["E"] = {e.path.strip("/").split("/")[-1]: e for e in E}
    e = size_holder["E"][name]
    b = bytearray()
    with open(IMG, "rb") as f:
        for i in range(math.ceil(e.size / UDS)):
            f.seek((e.lba + i) * SEC); b += f.read(SEC)[UDO:UDO + UDS]
    return bytearray(b[:e.size])


def pv_elems(buf, s):
    end, toks = PV(bytes(buf), s)
    out = []; cur = []
    for t in toks:
        if t.kind == 'glyph':
            r = t.raw
            cur.append(r[0] if len(r) == 1 else ((r[0] - 0xEB) << 8) | r[1])
        else:
            if cur: out.append(('r', cur)); cur = []
            out.append(('c', bytes(t.raw)))
    if cur: out.append(('r', cur))
    # 종결자 0xFF 는 요소에서 뺀다 (재조립 때 따로 붙인다). 이걸 빼지 않으면
    # '끝의 전부-공백 정크 런'이 마지막 요소가 아니게 돼 제거 로직이 죽는다.
    if out and out[-1] == ('c', b'\xff'):
        out.pop()
    return out, end


def adv_of(g):
    a = 0; ph = 0
    for i in g:
        if i < 0x101: a += 1
        else: a += 1 + ph; ph ^= 1
    return a


def enc_glyphs(g):
    o = bytearray()
    for i in g:
        o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
    return bytes(o)


def rec_end_dlg(b, s):
    p = s
    while p < len(b):
        x = b[p]
        if x == 0xFF:
            return p + 1
        p += 1 if x < 0xEB else (2 if x <= 0xF5 else 1 + CTRL.get(x, 0))
    return s


def glyphs_dlg(b, s, e):
    out = []; p = s
    while p < e - 1:
        x = b[p]
        if x < 0xEB: out.append(x); p += 1
        elif x <= 0xF5: out.append(((x - 0xEB) << 8) | b[p + 1]); p += 2
        else: p += 1 + CTRL.get(x, 0)
    return out


def despace(t):
    return t.replace(" ", "").replace("\u3000", "")


class Fixer:
    def __init__(self, name, cfg, sec):
        self.name = name; self.cfg = cfg; self.sec = sec
        self.ret = open(cfg["retail"], "rb").read()
        self.cur = rd_iso(cfg["iso"])
        assert len(self.ret) == len(self.cur)
        gm = add_extra_glyph_mapping(load_safe_glyph_map(), cfg["extras"])
        self.gm = gm
        self.inv = {}
        for i, ch in I2C.items():
            if i < 0x101 and ch:
                self.inv[i] = ch
        for ch, i in gm.items():
            self.inv.setdefault(i, ch)
        self.overrides = {}
        op = f"{SP}/tr/menu_align_overrides.json"
        if os.path.exists(op):
            self.overrides = json.load(open(op, encoding="utf-8"))
        self.writes = []          # (start, end) 이 스크립트가 쓴 범위
        self.fails = []

    # ---------- 폰트/도너 ----------
    def _font_slot(self, buf, g):
        o = self.cfg["font"] + g * GB
        return bytes(buf[o:o + GB])

    def prepare_space(self):
        cfg = self.cfg
        dyn = open(cfg["dyn"], "rb").read()
        assert len(dyn) == GB * GC
        live = set()
        # 살아있는 글리프 참조: 테이블(대사 문법) + ui_master(PV)
        for ptr, cnt in cfg["tables"]:
            for k in range(cnt):
                f = ptr + 4 + 4 * k
                t = f + struct.unpack_from("<i", self.cur, f)[0]
                if 0x800 <= t < len(self.cur):
                    live.update(glyphs_dlg(self.cur, t, rec_end_dlg(self.cur, t)))
        MH = cfg["MH"]
        for k in range(107):
            f = MH + 4 + 4 * k
            for buf in (self.cur, self.ret):
                t = f + struct.unpack_from("<i", buf, f)[0]
                el, _ = pv_elems(buf, t)
                live.update(i for tk, v in el if tk == 'r' for i in v)
        self.live = live
        extras_end = 0xA2F + len(cfg["extras"])
        free = []
        for g in range(extras_end, GC):
            if g in live:
                continue
            if self._font_slot(self.cur, g) == dyn[g * GB:(g + 1) * GB]:
                free.append(g)
        # 합자 리매핑: 레트일 ui_master 가 참조하는 고슬롯 중 비트맵이 덮인 것
        self.remap = {}
        high = sorted(i for i in live if i > 0xA38 and i != 0x3FF and i <= 0xAFF)
        for g in high:
            if self._font_slot(self.cur, g) == self._font_slot(self.ret, g):
                continue                      # 그대로 살아 있음 (제3차 대부분)
            ng = next(x for x in free if x <= 0xAFE and (x & 0xFF) != 0xFF)
            free.remove(ng)
            o = cfg["font"] + ng * GB
            self.cur[o:o + GB] = self._font_slot(self.ret, g)
            self.writes.append((o, o + GB))
            self.remap[g] = ng
        # 도너 바이트 풀
        self.pool = [[cfg["font"] + g * GB, cfg["font"] + (g + 1) * GB] for g in free]
        # 연속 슬롯 병합
        merged = []
        for a, b in sorted(self.pool):
            if merged and merged[-1][1] == a:
                merged[-1][1] = b
            else:
                merged.append([a, b])
        self.pool = merged
        self.pool_total = sum(b - a for a, b in self.pool)
        print(f"  도너 {self.pool_total:,}B ({len(self.pool)}블록)  "
              f"합자 리매핑 {len(self.remap)}건 {{{', '.join(f'{a:#x}→{b:#x}' for a, b in self.remap.items())}}}")

    def alloc(self, n):
        for blk in self.pool:
            if blk[1] - blk[0] >= n:
                pos = blk[0]; blk[0] += n
                return pos
        raise SystemExit(f"{self.name}: 도너 부족 ({n}B)")

    # ---------- 텍스트 ----------
    def jp_text(self, g):
        out = []
        for i in g:
            if i == 0:
                continue
            ch = I2C.get(i, "")
            if not ch:
                return None
            out.append(ch)
        return "".join(out)

    def ko_text(self, g):
        out = []
        for i in g:
            if i == 0 or i == 0x3FF:
                # 0x3FF 는 이전 주입기가 넣은 전각 패딩(EE FF) — 공백 취급
                continue
            ch = self.inv.get(i, "")
            if not ch:
                return None
            out.append(ch)
        return "".join(out)

    def enc_text(self, t):
        g = []
        for ch in normalise_for_font(t)[0]:
            i = self.gm.get(ch)
            if i is None:
                return None
            g.append(i)
        return g

    # ---------- 레코드 교정 ----------
    def fix_record(self, k):
        MH = self.cfg["MH"]
        f = MH + 4 + 4 * k
        rt = f + struct.unpack_from("<i", self.ret, f)[0]
        pt = f + struct.unpack_from("<i", self.cur, f)[0]
        rel, rend = pv_elems(self.ret, rt)
        cel, cend = pv_elems(self.cur, pt)
        ret_rec = bytes(self.ret[rt:rend])
        cur_rec = bytes(self.cur[pt:cend])

        # 1) 제2차 통째 이식 (글리프 게이트)
        sec = self.sec
        if ret_rec in sec["rec_map"]:
            ok = True
            for g in sec["rec_glyphs"][ret_rec]:
                if g <= 0xA38 or g == 0x3FF:
                    continue
                if self._font_slot(self.cur, g) != sec["sec_font"][g * GB:(g + 1) * GB]:
                    ok = False; break
            if ok:
                return sec["rec_map"][ret_rec], "이식"

        # 2) 런 단위 교정
        rruns = [v for t2, v in rel if t2 == 'r']
        cruns = [v for t2, v in cel if t2 == 'r']
        rctls = [v for t2, v in rel if t2 == 'c']
        cctls = [v for t2, v in cel if t2 == 'c']
        # 제자리 패딩 잔재: 끝의 전부-공백 런 제거
        while len(cruns) > len(rruns) and cel and cel[-1][0] == 'r' \
                and all(i == 0 for i in cel[-1][1]):
            cel.pop(); cruns.pop()
        if len(cruns) != len(rruns) or len(cctls) != len(rctls):
            self.fails.append((k, "구조", f"런 {len(rruns)}/{len(cruns)} 컨트롤 {len(rctls)}/{len(cctls)}"))
            return None, None

        rules = {(p, o, n): w for p, o, n, w in sec["ctl_rules"]}
        out = bytearray(); ri = 0; ci = 0
        for t2, v in rel:
            if t2 == 'c':
                prev = rctls[ci - 1] if ci else b""
                nxt = rctls[ci + 1] if ci + 1 < len(rctls) else b""
                out += rules.get((prev, v, nxt), v)
                ci += 1
                continue
            rg = v; cg = [self.remap.get(i, i) for i in cruns[ri]]; ri += 1
            target = adv_of(rg)
            jt = self.jp_text(rg)
            lead = 0
            while lead < len(rg) and rg[lead] == 0:
                lead += 1

            def fit(text):
                eg = self.enc_text(text)
                if eg is None:
                    return None
                full = [0] * lead + eg
                a = adv_of(full)
                if a > target:
                    return None
                return enc_glyphs(full) + b"\x00" * (target - a)

            # 게임 간 어휘 통일: 제2차 번역이 있으면 폭이 맞는 한 항상 그걸 쓴다
            if jt and jt in sec["pairs"]:
                e = fit(sec["pairs"][jt])
                if e is not None:
                    out += e
                    continue
            if adv_of(cg) == target:
                out += enc_glyphs(cg)
                continue
            if 0x3FF in rg:
                # 레트일 쪽 스페이서는 구조적 칸 구분자 — 자동 교정 금지
                self.fails.append((k, "스페이서", jt)); return None, None
            kt = self.ko_text(cg)
            cands = []
            if kt:
                d = despace(kt)
                if d != kt:
                    cands.append(d)
                cands.append(kt)
            if jt:
                cands += self.overrides.get(jt, [])
            done = False
            for c in cands:
                e = fit(c)
                if e is not None:
                    out += e
                    done = True
                    break
            if not done:
                best = min((adv_of([0] * lead + (self.enc_text(c) or [0x3FF] * 40))
                            for c in cands), default=-1)
                self.fails.append((k, "폭", f"'{jt}' -> {cands[:2]} target={target} best={best}"))
                return None, None
        out.append(0xFF)
        return bytes(out), "런교정"

    # 순차 윈도우-워크 앵커: 포인터 없는 '외래' 레코드가 이들 바로 뒤에 이어 붙어
    # 게임이 순차로 훑는다. 그래서 크기를 늘리면(도너 이동 포함) 뒤 외래 레코드가
    # 밀려 깨진다. → 제자리 + '정확히 같은 크기'로만 교정 가능. 초과하면 보류.
    ANCHORS = {10, 11, 12, 20, 21, 22}

    def run(self):
        MH = self.cfg["MH"]
        stats = {"이식": 0, "런교정": 0, "동일": 0, "실패": 0,
                 "앵커교정": 0, "앵커보류": 0, "제자리": 0, "도너": 0}
        self.anchor_hold = []
        pending = []
        for k in range(107):
            new, how = self.fix_record(k)
            f = MH + 4 + 4 * k
            pt = f + struct.unpack_from("<i", self.cur, f)[0]
            _, cend = pv_elems(self.cur, pt)
            if k in self.ANCHORS:
                # 앵커: 정확히 같은 크기일 때만 제자리 교정, 아니면 현행 유지
                if new is not None and new != bytes(self.cur[pt:cend]) \
                        and len(new) == cend - pt:
                    self.cur[pt:pt + len(new)] = new
                    self.writes.append((pt, pt + len(new)))
                    stats["앵커교정"] += 1
                elif new is None or len(new) > cend - pt:
                    self.anchor_hold.append(k)
                    stats["앵커보류"] += 1
                continue
            if new is None:
                stats["실패"] += 1
                continue
            if new == bytes(self.cur[pt:cend]):
                stats["동일"] += 1
                continue
            # 제자리 용량: 원래 위치라면 레트일 레코드 전체 범위를 쓸 수 있다
            rt = f + struct.unpack_from("<i", self.ret, f)[0]
            if pt == rt:
                _, rend = pv_elems(self.ret, rt)
                cend = max(cend, rend)
            pending.append((k, new, how, pt, cend))
            stats[how] += 1
        # 배치: 제자리 우선, 넘치면 도너 (같은 내용은 도너 공유)
        placed = {}; donor_used = 0
        for k, new, how, pt, cend in pending:
            if len(new) <= cend - pt:
                self.cur[pt:pt + len(new)] = new
                self.writes.append((pt, pt + len(new)))
                stats["제자리"] += 1
                continue
            if new in placed:
                pos = placed[new]
            else:
                pos = self.alloc(len(new))
                self.cur[pos:pos + len(new)] = new
                self.writes.append((pos, pos + len(new)))
                placed[new] = pos; donor_used += len(new)
            f = MH + 4 + 4 * k
            struct.pack_into("<i", self.cur, f, pos - f)
            self.writes.append((f, f + 4))
            stats["도너"] += 1
        print(f"  레코드: 이식 {stats['이식']} / 런교정 {stats['런교정']} / "
              f"변경불요 {stats['동일']} / 실패 {stats['실패']}")
        print(f"  앵커: 제자리교정 {stats['앵커교정']} / 크기제약보류 {stats['앵커보류']} "
              f"{self.anchor_hold}")
        print(f"  배치: 제자리 {stats['제자리']} / 도너 {stats['도너']} ({donor_used:,}B)")
        return stats

    # ---------- 검증 ----------
    def verify(self, orig):
        MH = self.cfg["MH"]
        bad = []
        rules = {(p, o, n): w for p, o, n, w in self.sec["ctl_rules"]}
        allowed_new = set(rules.values())
        allowed_old = set(k[1] for k in rules)
        adv_bad = ctl_bad = 0
        for k in range(107):
            if k in getattr(self, "anchor_hold", []):
                continue          # 크기제약으로 현행 유지한 앵커 (별도 보고)
            f = MH + 4 + 4 * k
            rt = f + struct.unpack_from("<i", self.ret, f)[0]
            pt = f + struct.unpack_from("<i", self.cur, f)[0]
            rel, _ = pv_elems(self.ret, rt)
            try:
                cel, _ = pv_elems(self.cur, pt)
            except Exception as ex:
                bad.append(f"[{k}] PV 실패 {ex}"); continue
            rr = [v for t, v in rel if t == 'r']; pr = [v for t, v in cel if t == 'r']
            rc = [v for t, v in rel if t == 'c']; pc = [v for t, v in cel if t == 'c']
            while len(pr) > len(rr) and cel and cel[-1][0] == 'r' \
                    and all(i == 0 for i in cel[-1][1]):
                cel.pop(); pr.pop()      # 끝 정크(무해)는 비교에서 제외
            if len(rr) != len(pr) or len(rc) != len(pc):
                bad.append(f"[{k}] 구조 {len(rr)}/{len(pr)} {len(rc)}/{len(pc)}"); continue
            for i, (a, b) in enumerate(zip(rr, pr)):
                if adv_of(a) != adv_of(b):
                    adv_bad += 1; bad.append(f"[{k}] run{i} adv {adv_of(a)}->{adv_of(b)}")
            for i, (a, b) in enumerate(zip(rc, pc)):
                if a != b and not (a in allowed_old and b in allowed_new):
                    ctl_bad += 1; bad.append(f"[{k}] ctl{i} {a.hex(' ')}→{b.hex(' ')}")
            for t, v in cel:
                if t == 'r':
                    for g in v:
                        if g > 0xAFF:
                            bad.append(f"[{k}] 글리프 범위 밖 {g:#x}")
        # 변경 위치가 기록된 쓰기 범위 안에만 있는지
        w = sorted(self.writes)
        stray = 0
        i = 0
        diffs = [i for i in range(len(orig)) if orig[i] != self.cur[i]]
        for d in diffs:
            if not any(a <= d < b for a, b in w):
                stray += 1
        print(f"  검증: advance 어긋남 {adv_bad} / 비의도 ctl {ctl_bad} / "
              f"기록 외 변경 {stray}바이트 / 기타 {len(bad)-adv_bad-ctl_bad}")
        if bad[:10]:
            for x in bad[:10]:
                print("    -", x)
        return not bad and stray == 0


FIXED: dict[str, bytes] = {}


def main():
    sec = ST.load()
    results = {}
    for name, cfg in GAMES.items():
        print(f"== {name}")
        fx = Fixer(name, cfg, sec)
        orig = bytes(fx.cur)
        fx.prepare_space()
        fx.run()
        ok = fx.verify(orig)
        # 앵커 보류(크기제약)로 인한 fix_record 실패는 정상 — 별도 집계에서 뺀다
        real_fails = [x for x in fx.fails if x[0] not in fx.anchor_hold]
        for k, kind, msg in real_fails:
            print(f"    실패 [{k}] {kind}: {msg}")
        os.makedirs(f"{SP}/tr/fix", exist_ok=True)
        out = f"{SP}/tr/fix/{name}.war"
        open(out, "wb").write(bytes(fx.cur))
        results[name] = (ok, len(real_fails))
        FIXED[name] = bytes(fx.cur)
        print(f"  -> {out}")
    print("\n요약:", {k: ("OK" if v[0] and v[1] == 0 else f"실패 {v[1]}") for k, v in results.items()})
    bad = [k for k, v in results.items() if not v[0] or v[1]]
    if bad:
        raise SystemExit(f"메뉴 정렬 교정 실패: {bad}")
    return FIXED


if __name__ == "__main__":
    main()
