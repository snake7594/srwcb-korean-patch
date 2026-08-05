# -*- coding: utf-8 -*-
"""제2차(검증된 기준)의 ui_master 번역을 제3차/EX/TR 에 이식하기 위한 데이터.

전수 감사 결과 제2차만 '모든 텍스트 런의 renderer advance == 레트일'을 지켰고
(어긋남 0건), 제3차/EX/TR 은 각각 54~56건이 어긋나 커서 상대 앵커(FC dx dy)가
밀리며 메뉴 칸이 게임마다 다르게 틀어졌다.

산출물(pickle):
  rec_map    : 레트일 레코드 바이트 → 제2차 패치본 레코드 바이트 (통째 이식용;
               제2차의 앵커 인자 보정 FC FC 02→FD 등까지 포함)
  rec_glyphs : rec_map 값이 참조하는 글리프 인덱스 집합 (PV 파서 기준 —
               F0~F5 는 UI-VM 옵코드라 대사 문법으로 세면 오검출된다)
  ctl_rules  : (이전ctl, 원본ctl, 다음ctl) → 새 ctl. 통째 이식이 안 되는
               레코드(TR 변형 등)에 문맥으로 적용.
  pairs      : 레트일 런 텍스트(패딩 제거) → (제2차 한글 텍스트, 목표 advance)
  sec_font   : 제2차 패치본 폰트 블롭 (고슬롯 합자 게이트 비교용)

주의: 제2차 폰트의 0xA39+ 는 한글로 덮이지 않고 살아남은 '레트일 합자 글리프'
(誕生日 등 여러 한자가 한 칸에 든 특수 글리프)를 레트일 레코드가 직접 참조한다.
EX/TR 은 그 자리에 자기 extras 가 있어 통째 이식 전에 글리프 게이트가 필요하다.
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
import json, math, pickle, struct, sys, os
from pathlib import Path

SP = str(_P.BUILD)   # 중간 산출물(캐시·교정본)을 두는 곳
ROOT = str(_P.WORK)
sys.path.insert(0, str(_P.TOOLS))
CACHE = f"{SP}/tr/second_ui_maps.pkl"
os.makedirs(f"{SP}/tr", exist_ok=True)
SEC_HDR, SEC_CNT = 0x24320, 107
FONT_SEC = 0x28058
GLYPH_BYTES, GLYPH_COUNT = 32, 2816


def pv_elements(buf, s):
    """PV 파서로 레코드를 [('c', raw)|('r', (glyph,...))] 로. 끝오프셋도 반환."""
    from patch_second_exe_ui import parse_second_ui_vm_record as PV
    end, toks = PV(buf, s)
    out = []; cur = []
    for t in toks:
        if t.kind == 'glyph':
            r = t.raw
            cur.append(r[0] if len(r) == 1 else ((r[0] - 0xEB) << 8) | r[1])
        else:
            if cur: out.append(('r', tuple(cur))); cur = []
            out.append(('c', bytes(t.raw)))
    if cur: out.append(('r', tuple(cur)))
    return out, end


def adv_of(glyphs):
    adv = 0; ph = 0
    for i in glyphs:
        if i < 0x101: adv += 1
        else: adv += 1 + ph; ph ^= 1
    return adv


def enc_glyphs(glyphs):
    o = bytearray()
    for i in glyphs:
        o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
    return bytes(o)


def build():
    from second_translation_codec import load_safe_glyph_map, add_extra_glyph_mapping
    mp = json.load(open(f"{ROOT}/research/srwcb_embedded_font_mapping_reviewed.json",
                        encoding="utf-8"))
    I2C = {r["glyph_index"]: (r.get("character") or "") for r in mp["rows"]}
    gm = add_extra_glyph_mapping(load_safe_glyph_map(),
                                 ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응'])
    INV = {}
    for ch, ix in gm.items():
        INV.setdefault(ix, ch)

    sret = open(f"{ROOT}/extracted/SECOND/SECOND.WAR", "rb").read()
    if SECOND_PATCHED is None:
        raise SystemExit("제2차 패치본을 넘겨주세요 (second_ui_transplant.SECOND_PATCHED)")
    spat = bytes(SECOND_PATCHED)

    def jp_text(glyphs):
        return "".join(I2C.get(i, "\uFFFD") for i in glyphs)

    def ko_text(glyphs):
        return "".join(INV.get(i, "\uFFFD") for i in glyphs)

    rec_map = {}; rec_glyphs = {}; ctl_rules = []; pairs = {}
    for k in range(SEC_CNT):
        f = SEC_HDR + 4 + 4 * k
        rt = f + struct.unpack_from("<i", sret, f)[0]
        pt = f + struct.unpack_from("<i", spat, f)[0]
        re_, rend = pv_elements(sret, rt)
        pe_, pend = pv_elements(spat, pt)
        rb = bytes(sret[rt:rend]); pb = bytes(spat[pt:pend])
        rr = [v for t, v in re_ if t == 'r']; pr = [v for t, v in pe_ if t == 'r']
        rc = [v for t, v in re_ if t == 'c']; pc = [v for t, v in pe_ if t == 'c']
        assert len(rr) == len(pr) and len(rc) == len(pc), f"제2차 [{k}] 구조 불일치"
        for a, b2 in zip(rr, pr):
            assert adv_of(a) == adv_of(b2), f"제2차 [{k}] 런 advance 불일치"
            jt = jp_text(a).strip("␣").strip()
            jt = jt.strip("\x00")
            # 패딩(0x000) 제거한 순수 텍스트 키
            core_a = [i for i in a if i != 0]
            core_b = [i for i in b2 if i != 0]
            if not core_a or a == b2:
                continue
            kt = ko_text(core_b)
            if "\uFFFD" in jp_text(core_a) or "\uFFFD" in kt:
                continue           # 합자/특수 글리프가 낀 런은 텍스트 짝으로 못 씀
            pairs.setdefault(jp_text(core_a), kt)
        for i, (a, b2) in enumerate(zip(rc, pc)):
            if a != b2:
                prev = rc[i - 1] if i else b""
                nxt = rc[i + 1] if i + 1 < len(rc) else b""
                ctl_rules.append((prev, a, nxt, b2))
        rec_map[rb] = pb
        rec_glyphs[rb] = {i for t, v in pe_ if t == 'r' for i in v}
    seen = {}
    for prev, old, nxt, new in ctl_rules:
        key = (prev, old, nxt)
        assert seen.setdefault(key, new) == new, f"ctl 규칙 충돌 {key}"
    sec_font = spat[FONT_SEC:FONT_SEC + GLYPH_BYTES * GLYPH_COUNT]
    pickle.dump({"rec_map": rec_map, "rec_glyphs": rec_glyphs,
                 "ctl_rules": ctl_rules, "pairs": pairs, "sec_font": sec_font},
                open(CACHE, "wb"))
    print(f"제2차 이식 데이터: 레코드 {len(rec_map)}종 / 텍스트 짝 {len(pairs)}종 / "
          f"앵커 규칙 {len(ctl_rules)}건")
    for prev, old, nxt, new in ctl_rules:
        print(f"   ctl {old.hex(' ')} -> {new.hex(' ')}  (앞 {prev.hex(' ')} / 뒤 {nxt.hex(' ')})")
    return CACHE


# 빌드 파이프라인이 넘겨주는 제2차 패치본 (이미지 대신 쓴다).
SECOND_PATCHED = None


def load():
    if not os.path.exists(CACHE):
        build()
    return pickle.load(open(CACHE, "rb"))


if __name__ == "__main__":
    build()
