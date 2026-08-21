# -*- coding: utf-8 -*-
"""이벤트 스크립트에서 **포인터 피연산자가 아닌 바이트**가 바뀌지 않았는지 본다.

시나리오 블록은 `[스크립트][텍스트 풀]` 이고, 재배치는 **스크립트 길이를 그대로 두고**
풀만 다시 만든다. 그러니 스크립트 구간에서 레트일과 달라질 수 있는 바이트는
**대사 포인터의 2바이트 피연산자뿐**이다. 그 밖의 바이트가 바뀌었다면 스캐너가
명령을 잘못 짚어 **옵코드를 덮어쓴** 것이다.

## 이 게이트가 태어난 이유 (2026-08-21)

`F0 00 <변위16>` 은 공통 서브루틴을 부르는 4바이트 명령이고 스크립트에 아주 많다.
그 변위 바이트가 우연히 포인터 옵코드처럼 보이면 스캐너가 그 자리를 옵코드로 인정하고
**옵코드+2(= 다음 명령)** 를 변위로 덮어썼다.

    레트일  f0 00 b6 00 | b9 02 63 7a      `b6 00` 은 F0 의 변위
    v0.11.38  f0 00 b6 00 | 73 03 63 7a    B9 02 옵코드가 사라졌다

레트일 변위가 우연히 레코드 시작을 정확히 겨눠서 재조준기의 필터도 통과했다.
EX sc23 이 그 경우였고, 볼크루스 전투 뒤 스크립트가 통째로 탈선해 인터프리터가
빈 힙을 기어다녔다 — 커서 소실·입력 무반응·음악은 계속. 전수 조사 결과
**EX 6곳 · 제3차 2곳 · 제2차 0곳**. `iter_pointer_sites` 가 F0 변위 자리를 건너뛰도록
고쳤고, 다시 생기지 않도록 여기서 막는다.
"""
import os
import sys
from pathlib import Path

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools", "image-build"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P                                    # noqa: E402
import assemble_image as AI                                 # noqa: E402
from analyze_sce_relocation import (parse_scenarios,        # noqa: E402
                                    iter_pointer_sites)

SCE = (("EX/E_SCE.BIN", "EX"),
       ("THIRD/3_SCE.BIN", "제3차"),
       ("SECOND/2_SCE.BIN", "제2차"))


def check_file(ko: bytes, jp: bytes, label: str) -> int:
    sj, sk = parse_scenarios(jp), parse_scenarios(ko)
    if len(sj) != len(sk):
        print(f"  [실패] {label}: 시나리오 수 {len(sj)} -> {len(sk)}")
        return 1
    bad = 0
    for a, b in zip(sj, sk):
        n = a.pool_start - a.block_start
        if n != b.pool_start - b.block_start:
            print(f"  [실패] {label} sc{a.index}: 스크립트 길이 {n} -> "
                  f"{b.pool_start - b.block_start}")
            bad += 1
            continue
        allowed = bytearray(n)
        for _off, operand, _op in iter_pointer_sites(jp, a.block_start, a.pool_start):
            for k in (0, 1):
                i = operand - a.block_start + k
                if 0 <= i < n:
                    allowed[i] = 1
        for i in range(n):
            if jp[a.block_start + i] != ko[b.block_start + i] and not allowed[i]:
                ctx_j = jp[a.block_start + i - 4:a.block_start + i + 4].hex(" ")
                ctx_k = ko[b.block_start + i - 4:b.block_start + i + 4].hex(" ")
                print(f"  [실패] {label} sc{a.index} 블록+0x{i:04X}: 포인터 피연산자가"
                      f" 아닌 바이트가 바뀜\n         레트일 {ctx_j}\n         한글   {ctx_k}")
                bad += 1
    return bad


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v0.11.0")
    ap.add_argument("--image", type=Path)
    a = ap.parse_args()
    img = a.image or (_P.OUT /
                      f"Super Robot Taisen Complete Box Korean {a.version} (Track 1).bin")
    if not img.exists():
        raise SystemExit(f"[없음] 이미지: {img}")
    with AI.RawMode2Image(img) as m:
        _, entries = AI.read_tree(m)
    by = {e.path.strip("/"): e for e in entries}
    bad = 0
    for rel, label in SCE:
        if rel not in by:
            continue
        ko = AI.read_file(img, by[rel].lba, by[rel].size)
        jp = (_P.EXTRACTED / rel).read_bytes()
        n = check_file(ko, jp, label)
        bad += n
        if not n:
            print(f"  {label:5} 이벤트 스크립트: 포인터 피연산자 외 변경 없음")
    if bad:
        raise SystemExit(f"이벤트 스크립트 검증 실패 {bad}건")
    print("이벤트 스크립트 검증 통과")


if __name__ == "__main__":
    main()
