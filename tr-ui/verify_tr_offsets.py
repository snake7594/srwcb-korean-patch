# -*- coding: utf-8 -*-
"""inject_tr_ui.py 가 쓰는 TR 오프셋이 정말 EX 의 대응 위치인지 못 박는다.

오프셋을 상수 델타로 추측했다가 틀리면 엉뚱한 바이트를 덮어써서 화면이
통째로 깨진다(제3차·EX 에서 이미 겪었다). 그래서 주입 전에 **레코드 단위로
내용이 같은지** 전부 확인한다. 표의 원시 바이트를 통째로 비교하면 안 된다 —
포인터 배열 뒤 풀의 끝 위치가 조금 다르고 그 뒤엔 무관한 데이터가 온다.
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
# ------------------------------------------------------------------
import struct, sys

ROOT = str(_P.WORK)
EX = open(f"{ROOT}/extracted/EX/EX.WAR", "rb").read()
TR = open(f"{ROOT}/extracted/TR.WAR", "rb").read()
BASE = 0x8000f800
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}

# (이름, EX헤더, TR헤더, 개수, TR상한)  ※ 상한은 실제 풀 끝 이상이어야 한다
TABLES = [
    ("terrain_names",     0x0bcb4, 0x0bcac,  144, 0x0c17c),
    ("spirit_commands",   0x0c184, 0x0c17c,   94, 0x0c848),
    ("enhancement_parts", 0x0c850, 0x0c848,   64, 0x0cbc4),
    ("weapon_names",      0x0cbcc, 0x0cbc4, 1344, 0x0f250),
    ("pilot_skills",      0x0f258, 0x0f250,   52, 0x0f508),
    ("unit_abilities",    0x0f510, 0x0f508,   22, 0x0f614),
    ("scenario_titles",   0x0f61c, 0x0f614,  192, 0x0fbfc),
    ("pilot_short_names", 0x10778c, 0x107768, 400, 0x108198),
    ("pilot_full_names",  0x1081bc, 0x108198, 400, 0x108f40),
    ("unit_names",        0x108f64, 0x108f40, 448, 0x109fdc),
]
POINTED = [("ui_master", 0x188c4, 0x188bc, 107), ("type_table", 0x095b8, 0x095b4, 15)]
# TR 에만 다른 레코드. [39] 시스템 설정창은 트레이닝 모드에 '퀵 컨티뉴' 안내 두 줄이
# 없어서 30바이트 짧다. 구성 스팬은 전부 span_map 에 있으므로 주입에는 문제없다.
KNOWN_DIFF = {("ui_master", 39)}
ANCHORS = [("cmd_menu", 0x974b, 0x9747, 77), ("yesno", 0x0971c, 0x09718, 8)]


def rec_end(b, s):
    p = s
    while p < len(b):
        x = b[p]
        if x == 0xFF:
            return p + 1
        p += 1 if x < 0xEB else (2 if x <= 0xF5 else 1 + CTRL.get(x, 0))
    return s


def tgt(d, h, k):
    f = h + 4 + 4 * k
    return f + struct.unpack_from("<i", d, f)[0]


def check_table(name, he, ht, cnt, bound, bad):
    for nm, d, h in (("EX", EX, he), ("TR", TR, ht)):
        v = struct.unpack_from("<I", d, h)[0]
        if v != BASE + h + 4:
            bad.append(f"{name}: {nm} 헤더가 자기참조가 아님 ({v:#x})")
    lo = ht + 4 + 4 * cnt
    pool_end = 0
    for k in range(cnt):
        te, tt = tgt(EX, he, k), tgt(TR, ht, k)
        if not (lo <= tt < len(TR)):
            bad.append(f"{name}[{k}]: TR 포인터가 풀 밖 ({tt:#x})"); return
        ee, et = rec_end(EX, te), rec_end(TR, tt)
        if EX[te:ee] != TR[tt:et]:
            bad.append(f"{name}[{k}]: 레코드 내용 불일치 (EX {te:#x} / TR {tt:#x})"); return
        pool_end = max(pool_end, et)
    if pool_end > bound:
        bad.append(f"{name}: 상한 {bound:#x} 이 실제 풀 끝 {pool_end:#x} 보다 작다")


def main():
    bad = []
    for name, he, ht, cnt, bound in TABLES:
        check_table(name, he, ht, cnt, bound, bad)
    noted = []
    for name, he, ht, cnt in POINTED:
        for k in range(cnt):
            te, tt = tgt(EX, he, k), tgt(TR, ht, k)
            if not (0x800 <= tt < len(TR)):
                bad.append(f"{name}[{k}]: TR 포인터 범위 밖 ({tt:#x})"); break
            et = rec_end(TR, tt)
            if et <= tt or TR[et - 1] != 0xFF:
                bad.append(f"{name}[{k}]: TR 레코드가 0xFF 로 끝나지 않음 ({tt:#x})"); break
            if EX[te:rec_end(EX, te)] != TR[tt:et]:
                if (name, k) in KNOWN_DIFF:
                    noted.append(f"{name}[{k}] TR 전용 레코드 ({et-tt}B) — 예상된 차이")
                else:
                    bad.append(f"{name}[{k}]: 레코드 내용 불일치 (EX {te:#x} / TR {tt:#x})"); break
    for name, oe, ot, ln in ANCHORS:
        if EX[oe:oe + ln] != TR[ot:ot + ln]:
            bad.append(f"{name}: 앵커 바이트 불일치")
        elif TR.count(TR[ot:ot + ln]) != 1:
            bad.append(f"{name}: 앵커가 TR 안에서 유일하지 않음")

    if bad:
        print("!! 오프셋 검증 실패")
        for x in bad:
            print("   -", x)
        sys.exit(1)
    for x in noted:
        print("   *", x)
    total = sum(c for _, _, _, c, _ in TABLES) + sum(c for _, _, _, c in POINTED)
    print(f"오프셋 검증 통과: 테이블 {len(TABLES)}개 + 포인터표 {len(POINTED)}개 "
          f"({total:,} 레코드, 예상된 차이 {len(noted)}건 외 전부 EX 와 바이트 동일) "
          f"+ 앵커 {len(ANCHORS)}개")


if __name__ == "__main__":
    main()
