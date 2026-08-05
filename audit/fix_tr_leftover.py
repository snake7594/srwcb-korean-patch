# -*- coding: utf-8 -*-
"""TR.WAR 잔여 미번역 UI 문자열을 EX.WAR 에서 이식한다.

전면 재검증에서 트레이닝 모드 UI 문자열 다수가 일본어로 남아 있는 걸 발견했다
(부대표·발진표·출격 선택·레벨업·정신기 습득 등, 항상 화면에 뜨는 것들). TR 은
EX 의 쌍둥이라 EX 에는 같은 문자열이 이미 번역·폭검증된 상태로 들어 있다.

방식: TR 의 미번역 레코드마다 **레트일 바이트가 정확히 일치하는 EX 위치**를 찾아
(델타 후보 + 유일 검색), EX 패치본의 그 자리 바이트를 그대로 가져온다. EX 는
제자리(같은 길이) 번역이라 TR 에도 그대로 들어간다. 크기·포인터 전부 불변.
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
if str(_P.TOOLS) not in _sys.path:
    _sys.path.insert(0, str(_P.TOOLS))
# ------------------------------------------------------------------
import sys, struct, json
from pathlib import Path

R = str(_P.WORK)
sys.path.insert(0, f"{R}/tools")
sys.path.insert(0, str(Path(__file__).parent))
import audit_all as A

CB = A.CB
DELTAS = (4, 8, 0x24, 0, 0x30, -4, -8, 0x2c, 0x20)


def port(tr_war, tr_ret, ex_war, ex_ret, items, verbose=True):
    out = bytearray(tr_war)
    done = skip = ambig = same = 0
    details = []
    for x in items:
        t, e = x["off"], x["end"]
        src = tr_ret[t:e]
        if len(src) < 2:
            skip += 1; continue
        # 1) 델타 후보로 레트일 바이트 일치 확인
        p = None
        for d in DELTAS:
            q = t + d
            if 0 <= q and q + len(src) <= len(ex_ret) and ex_ret[q:q + len(src)] == src:
                p = q; break
        # 2) 실패 시 유일 검색
        if p is None:
            if ex_ret.count(src) == 1:
                p = ex_ret.find(src)
            else:
                ambig += 1; continue
        rep = ex_war[p:p + len(src)]
        if rep == src:
            same += 1; continue            # EX 도 미번역
        if bytes(out[t:e]) != src:
            skip += 1; continue            # TR 이 이미 손댄 자리
        out[t:t + len(rep)] = rep
        done += 1
        details.append((t, x["jp"][:24], A.decode(bytes(rep), 0, len(rep), A.ko_table(A.EX15))[:24]))
    if verbose:
        print(f"  이식 {done} / EX도미번역 {same} / 위치모호 {ambig} / 스킵 {skip}")
        for t, jp, ko in details[:18]:
            print(f"     {t:#07x} '{jp}' -> '{ko}'")
    return bytes(out), done, details


def main():
    tr_war = A.read_iso(CB, "TR.WAR")
    ex_war = A.read_iso(CB, "EX/EX.WAR")
    tr_ret = open(f"{R}/extracted/TR.WAR", "rb").read()
    ex_ret = open(f"{R}/extracted/EX/EX.WAR", "rb").read()
    items = json.load(open("audit_untranslated.json", encoding="utf-8"))["TR.WAR"]
    print(f"TR 미번역 후보 {len(items)}건 -> EX 이식")
    fixed, n, det = port(tr_war, tr_ret, ex_war, ex_ret, items)
    assert len(fixed) == len(tr_war), "크기 변동"
    open("TR_leftover_fixed.war", "wb").write(fixed)
    print(f"\nWROTE TR_leftover_fixed.war ({n}건 이식, 크기 {len(fixed):,} 불변)")


if __name__ == "__main__":
    main()
