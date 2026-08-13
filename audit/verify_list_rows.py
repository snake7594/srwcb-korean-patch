# -*- coding: utf-8 -*-
"""목록 화면 한 줄의 **커서 왕복이 맞는지** 확인한다 (build_all 8단계).

출격 유닛 선택·아군부대표 같은 목록은 한 줄을 이렇게 그린다.

    [F7]<창>  [FB ..][F8 01]=유닛명  [FC 13 fe]  [FB ..][F8 01]=파일럿명
              [FC 08 fe]  [F8 00]=LV  [FC 01 00]  [F8 83]=레벨  [FC dc 02]

마지막 `FC dc 02`(dx -36)는 **다음 줄 시작으로 돌아가는** 이동이다. 그래서 한
줄의 `FC dx` 합은 레트일 값 그대로여야 한다. 열 위치를 옮기겠다고 가운데 이동만
줄이면 합이 어긋나고, 줄마다 그만큼씩 **계단처럼 밀린다** — v0.11.31 이 그랬다
(출격 목록이 아래로 갈수록 왼쪽으로 밀려 화면 밖으로 나갔다).

레트일과 배포본의 dx 합을 목록 골격마다 대조한다.
"""
import os
import re
import sys
from pathlib import Path

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools", "image-build"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P            # noqa: E402
import assemble_image as AI         # noqa: E402

EXES = ["SECOND/SECOND.WAR", "THIRD/THIRD.WAR", "EX/EX.WAR", "TR.WAR", "SLPS_020.70"]

#: 목록 한 줄의 골격 — `FC`/`FB`/`F8` 만 남긴 뼈대로 찾는다. dx 값은 자유.
#:
#: ★ **창 열기 직전 이동(`FC dx ff`)까지 포함**해야 한다. 그것도 행 루프 안이라
#:   빼고 세면 거기서 생긴 어긋남을 놓친다 — v0.11.32 가 그렇게 새어 나갔다.
_ROW = re.compile(
    rb"\xfc(.)\xff\xf7\x00\x40\xfb..\xf8\x01\xfc(.)\xfe\xfb..\xf8\x01\xfc(.)\xfe"
    rb"\xf8\x00\xfc(.)\x00\xf8\x83\xfc(.)\x02", re.S)


def _s8(b):
    return b - 256 if b > 127 else b


def rows(buf):
    """[(위치, dx 목록, 합)] — 목록 행 골격을 전부."""
    out = []
    for m in _ROW.finditer(buf):
        dx = [_s8(m.group(i)[0]) for i in range(1, 6)]
        out.append((m.start(), dx, sum(dx)))
    return out


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
    for rel in EXES:
        e = by.get(rel)
        if e is None:
            continue
        ko = AI.read_file(img, e.lba, e.size)
        jp = (_P.EXTRACTED / rel).read_bytes()
        want = sorted({s for _o, _d, s in rows(jp)})
        got = sorted({s for _o, _d, s in rows(ko)})
        if want != got:
            print(f"  [실패] {rel}: 한 줄 dx 합 {got} — 레트일은 {want}")
            bad += 1
        else:
            print(f"  {rel:20} 목록 행 {len(rows(ko))}개, dx 합 {got} (레트일과 동일)")
    if bad:
        raise SystemExit(f"목록 행 커서 왕복 검증 실패 {bad}건 — 줄마다 밀립니다")
    print("목록 행 커서 왕복 검증 통과")


if __name__ == "__main__":
    main()
