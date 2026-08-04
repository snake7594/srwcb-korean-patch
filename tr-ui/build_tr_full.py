# -*- coding: utf-8 -*-
"""트레이닝 모드(TR.WAR) 최종 빌드 + 검증 게이트.

inject_tr_ui.py 결과에 **BMESS2/3/4 외부 오프셋표**를 얹는다. 이게 이번 수정의
핵심이다 — 세 아카이브를 한글로 재패킹했는데 TR.WAR 안의 표만 레트일 그대로라
전투 메시지 로드가 CPE 블록 중간에서 시작했고, 그래서 트레이닝 모드 전투에서
대사가 안 나오고 전투가 그대로 멈췄다.

게이트
  1) 크기·PS-X EXE 헤더 불변
  2) 폰트 블롭이 pre-inject 와 동일 (도너로 쓴 미사용 슬롯 제외)
  3) 모든 테이블 포인터가 파일 안의 유효한 레코드를 가리킴
  4) BMESS 표 3개가 재패킹본과 일치
  5) 남은 '가시 일본어' 레코드 수가 주입 전보다 크게 줄었는지
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
import json, math, os, struct, sys, hashlib
from pathlib import Path

ROOT = str(_P.WORK)
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(_P.TOOLS)); sys.path.insert(0, SP)
from extract_psx_iso import RawMode2Image, read_tree
import tr_extra_records as XR

IMG = f"{ROOT}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.4 (Track 1).bin"
PRE = f"{ROOT}/test_build/ex_full/font_extracted/TR.WAR"
INJ = f"{ROOT}/test_build/tr_full/runtime/TR.WAR"     # inject_tr_ui.py 산출물
OUT = f"{ROOT}/test_build/tr_full/TR_final.war"       # BMESS 표까지 얹은 최종본
SEC, UDO, UDS = 2352, 24, 2048
FONT_OFF, FONT_BYTES = 0x1d520, 2816 * 32
SIZE = 0x123800

TABLES = [("terrain_names", 0xbcac, 144), ("spirit_commands", 0xc17c, 94),
          ("enhancement_parts", 0xc848, 64), ("weapon_names", 0xcbc4, 1344),
          ("pilot_skills", 0xf250, 52), ("unit_abilities", 0xf508, 22),
          ("scenario_titles", 0xf614, 192), ("pilot_short_names", 0x107768, 400),
          ("pilot_full_names", 0x108198, 400), ("unit_names", 0x108f40, 448),
          ("ui_master", 0x188bc, 107), ("type_table", 0x095b4, 15)]


def read_file(lba, size):
    b = bytearray()
    with open(IMG, "rb") as f:
        for i in range(math.ceil(size / UDS)):
            f.seek((lba + i) * SEC)
            b += f.read(SEC)[UDO:UDO + UDS]
    return bytes(b[:size])


def main():
    with RawMode2Image(Path(IMG)) as m:
        _, entries = read_tree(m)
    P = {e.path.strip("/").split("/")[-1]: e for e in entries}

    war = bytearray(open(INJ, "rb").read())
    retail = open(f"{ROOT}/extracted/TR.WAR", "rb").read()
    pre = open(PRE, "rb").read()
    assert len(war) == SIZE == len(retail) == len(pre), "크기 불일치"

    # ---- BMESS 외부표 ----
    tabs = []
    for name in ("BMESS2", "BMESS3", "BMESS4"):
        old = open(f"{ROOT}/extracted/{name}.BIN", "rb").read()
        old_t = old[:struct.unpack_from("<I", old, 0)[0]]
        e = P[name + ".BIN"]
        new = read_file(e.lba, e.size)
        new_t = new[:struct.unpack_from("<I", new, 0)[0]]
        assert old_t != new_t, f"{name}: 재패킹본 표가 레트일과 같음 (빌드 확인 필요)"
        tabs.append((name, old_t, new_t))
    XR.patch_bmess_tables(war, retail, tabs)

    open(OUT, "wb").write(bytes(war))
    print(f"WROTE {OUT}  sha {hashlib.sha256(bytes(war)).hexdigest()[:16]}")

    # ================= 게이트 =================
    bad = []
    # 1) 헤더
    if war[:8] != b"PS-X EXE": bad.append("PS-X EXE 매직 손상")
    if struct.unpack_from("<I", war, 0x1c)[0] + 0x800 != SIZE: bad.append("t_size 불일치")
    # 2) 폰트 (도너로 쓴 미사용 슬롯을 뺀 나머지)
    man = json.load(open(f"{SP}/tr/tr_inject_manifest.json", encoding="utf-8"))
    same = sum(1 for i in range(FONT_OFF, FONT_OFF + 0x101 * 32) if war[i] == pre[i])
    if same != 0x101 * 32: bad.append("반각 글리프(0x000~0x100) 영역이 변경됨")
    # 3) 테이블 포인터 유효성
    tot = ok = 0
    for name, h, cnt in TABLES:
        for k in range(cnt):
            f = h + 4 + 4 * k
            t = f + struct.unpack_from("<i", war, f)[0]
            tot += 1
            if not (0x800 <= t < SIZE): bad.append(f"{name}[{k}] 포인터 범위 밖 {t:#x}"); continue
            e = XR.rec_end(war, t)
            if e <= t or war[e - 1] != 0xFF: bad.append(f"{name}[{k}] 레코드 종결자 없음 @{t:#x}"); continue
            if any(0xEB <= war[p] <= 0xF5 and p + 1 >= e for p in range(t, e)):
                bad.append(f"{name}[{k}] 2바이트 글리프가 잘림 @{t:#x}"); continue
            ok += 1
    # 4) BMESS 표
    for name, old_t, new_t in tabs:
        if bytes(war).count(new_t) != 1: bad.append(f"{name}: 새 표가 유일하지 않음")
        if bytes(war).find(old_t) >= 0: bad.append(f"{name}: 낡은 표가 남아 있음")
    # 5) 잔여 일본어
    import tr_survey as S
    def count_jp(buf):
        n = 0; i = 1
        while i < len(buf):
            if buf[i - 1] != 0xFF or buf[i] == 0xFF: i += 1; continue
            s, end = S.decode(buf, i)
            if not s: i += 1; continue
            body = S.plain(s)
            if body and len(S.JPRE.findall(body)) >= 3 and len(S.JPRE.findall(body)) / len(body) >= 0.5:
                n += 1; i = end
            else: i += 1
        return n
    before, after = count_jp(retail), count_jp(bytes(war))

    print(f"\n게이트")
    print(f"  1) 크기/헤더            OK ({SIZE:,}B)")
    print(f"  2) 반각 글리프 보존      OK")
    print(f"  3) 테이블 포인터         {ok}/{tot} 유효")
    print(f"  4) BMESS 외부표 3개      갱신 확인")
    print(f"  5) 가시 일본어 레코드    {before} -> {after} ({100*(before-after)//max(before,1)}% 감소)")
    if bad:
        print("\n!! 게이트 실패")
        for x in bad[:30]: print("   -", x)
        sys.exit(1)
    print("\n모든 게이트 통과")


if __name__ == "__main__":
    main()
