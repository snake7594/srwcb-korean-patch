# -*- coding: utf-8 -*-
"""최종 TR.WAR 안의 '실제로 그려지는 모든 글리프'가 살아있는 슬롯인지 검사한다.

주입기는 폰트에서 안 쓰는 글리프 슬롯을 도너(레코드 저장소)로 재활용한다.
어떤 레코드가 도너로 내준 슬롯을 가리키면 글자 대신 레코드 바이트가 그려져
화면이 깨지고, 심하면 멈춘다(제3차 v3 프리즈의 원인이 이 계열이었다).

그래서 최종 파일에서 **거꾸로** 확인한다:
  1) 도너로 실제 할당된 바이트 구간을 재계산한다
  2) 모든 테이블 포인터가 가리키는 레코드 + 도너에 재배치된 레코드를 훑어
     사용된 글리프 인덱스를 모은다
  3) 그 인덱스의 폰트 슬롯이 도너 구간과 겹치면 실패
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
import json, os, struct, sys

ROOT = str(_P.WORK)
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(_P.TOOLS)); sys.path.insert(0, SP)
import tr_extra_records as XR
from patch_second_exe_ui import parse_second_ui_vm_record as PV

WAR = f"{ROOT}/test_build/tr_full/TR_final.war"
PRE = f"{ROOT}/test_build/ex_full/font_extracted/TR.WAR"
FONT_OFF, GLYPH_BYTES, GLYPH_COUNT = 0x1d520, 32, 2816
TABLES = [("terrain_names", 0xbcac, 144), ("spirit_commands", 0xc17c, 94),
          ("enhancement_parts", 0xc848, 64), ("weapon_names", 0xcbc4, 1344),
          ("pilot_skills", 0xf250, 52), ("unit_abilities", 0xf508, 22),
          ("scenario_titles", 0xf614, 192), ("pilot_short_names", 0x107768, 400),
          ("pilot_full_names", 0x108198, 400), ("unit_names", 0x108f40, 448),
          ("ui_master", 0x188bc, 107), ("type_table", 0x095b4, 15)]


def glyph_indices(buf, s, e):
    """대사 문법 기준. 0xEB~0xF5 = 2바이트 글리프."""
    out = []
    p = s
    while p < e - 1:
        x = buf[p]
        if x < 0xEB:
            out.append(x); p += 1
        elif x <= 0xF5:
            out.append(((x - 0xEB) << 8) | buf[p + 1]); p += 2
        else:
            p += 1 + XR.CTRL_ARGS.get(x, 0)
    return out


def glyph_indices_uivm(buf, s):
    """ui_master 전용. ★여기서는 0xF0~0xF5 가 글리프 선두바이트가 아니라
    UI-VM 옵코드다. 대사 문법으로 읽으면 0x501/0x600/0xA3E 같은 존재하지 않는
    글리프를 쓰는 것처럼 보여 허위 경보가 뜬다."""
    out = []
    _e, toks = PV(buf, s)
    for t in toks:
        if t.kind == "glyph":
            r = t.raw
            out.append(r[0] if len(r) == 1 else ((r[0] - 0xEB) << 8) | r[1])
    return out


def main():
    war = open(WAR, "rb").read()
    pre = open(PRE, "rb").read()

    # 1) 폰트 영역에서 pre-inject 와 달라진 슬롯 = 도너로 쓰인 슬롯
    donor = set()
    for g in range(GLYPH_COUNT):
        a = FONT_OFF + g * GLYPH_BYTES
        if war[a:a + GLYPH_BYTES] != pre[a:a + GLYPH_BYTES]:
            donor.add(g)

    # 2) 살아있는 레코드가 쓰는 글리프
    used = set()
    reachable = []
    for name, h, cnt in TABLES:
        for k in range(cnt):
            f = h + 4 + 4 * k
            t = f + struct.unpack_from("<i", war, f)[0]
            if not (0x800 <= t < len(war)):
                continue
            e = XR.rec_end(war, t)
            reachable.append((name, k, t, e))
            if name == "ui_master":
                used.update(glyph_indices_uivm(war, t))
            else:
                used.update(glyph_indices(war, t, e))

    # 3) 겹침
    STRUCT = {0x3FF}
    clash = sorted(i for i in used if i in donor and i not in STRUCT)
    print(f"도너로 쓰인 글리프 슬롯 {len(donor)}개 / 레코드가 쓰는 글리프 {len(used)}종")
    print(f"살아있는 레코드 {len(reachable)}개")
    if clash:
        print(f"!! 충돌 {len(clash)}종: {clash[:20]}")
        sys.exit(1)
    hi = sorted(i for i in used if i >= 0x101)
    print(f"전각 글리프 사용 범위 {hi[0]:#x}~{hi[-1]:#x} — 도너와 겹침 없음")
    print("글리프 무결성 통과")


if __name__ == "__main__":
    main()
