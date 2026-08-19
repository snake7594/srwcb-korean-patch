# -*- coding: utf-8 -*-
"""UI 글자 **런**의 폭(advance)과 반칸(phase)을 레트일과 대조한다 (build_all 8단계).

`audit/verify_list_rows.py` 는 커서 이동(`FC dx dy`)의 합만 본다. 그런데 화면이
밀리는 원인은 이동만이 아니다 — 글자 자체가 몇 칸을 먹는지, 그리고 **반 칸이
남았는지**(phase)도 그만큼 위치를 바꾼다.

    전각 글리프는 `1 + phase` 칸 나아가고 phase 를 뒤집는다.
    반각 글리프·빈칸(0x00)은 1칸이고 phase 를 그대로 둔다.

그래서 전각 개수의 홀짝이 레트일과 다르면, 칸 수가 같아도 뒤따르는 것이 전부
**반 칸(4px)** 밀린다. 2026-08-19 제보에서 두 건이 여기 걸렸다.

    #16  유닛 능력 화면 `무기성능` — 앞 칸 `파일럿능력`(전각 5개)이 phase 를
         뒤집어 놔서 x 136 -> 140. 상자 오른쪽 테두리를 2px 침범했다.
    #18b 출격 머리글 — `출격유닛남음` 뒤를 반각 빈칸으로만 메워 14칸이어야 할
         것이 15칸이 됐다. `10기`·`기력 100`·`LV순` 이 통째로 8px 밀렸다.

두 건 다 기존 게이트를 전부 통과했다. 여기서 막는다.

## 죽은 사본을 어떻게 거르나

주입기가 레코드를 풀로 재배치하면 **옛 자리에 레트일 바이트가 그대로 남는다**.
그래서 KO 실행파일에는 같은 앵커가 두 벌 있다(한글 1 + 일본어 1). 런 바이트가
레트일 파일 안에 그대로 있으면 죽은 사본으로 보고 건너뛴다.
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

import srwcb_paths as _P                                  # noqa: E402
import assemble_image as AI                               # noqa: E402
from second_translation_codec import glyph_advance        # noqa: E402

EXES = ["SECOND/SECOND.WAR", "THIRD/THIRD.WAR", "EX/EX.WAR", "TR.WAR", "SLPS_020.70"]

#: (이름, 앵커 바이트, 런 끝 바이트, 앵커 꼬리에서 런에 포함할 바이트 수)
#:
#: 마지막 값은 '앵커를 길게 잡되 그 꼬리는 런의 일부'라는 뜻이다. 출격 머리글은
#: `fc 02 01 f8 00 0f f8 00` 까지만으로는 다른 곳에도 걸려서, 런 첫 글자인 선행
#: 반각 빈칸 `00` 까지 앵커에 넣어 유일하게 만들었다.
RUNS = [
    ("유닛능력 탭 줄", "f8 04 f7 00 40 fc f6 ee", "f6", 0),
    ("출격 머리글", "fc 02 01 f8 00 0f f8 00 00", "f8 82", 1),
]

#: 다시는 나오면 안 되는 바이트 — (이름, hex). 파일 전부에서 0곳이어야 한다.
FORBIDDEN = [
    ("유닛능력 탭 phase 어긋남", "ee a7 ed a1 ee b4 00 00 ef 59"),
    ("개조 확인 메시지 옛 문안", "f2 0c 3a ec 01 00 ee 00 3b f6 f4 de f2 0e 14 00 00"),
    ("출격 머리글 옛 문구(제2차형)",
     "f3 3c ec 3d f1 ef ed ae f0 79 f3 d9 00 ed 2c f1 fa"),
    ("출격 머리글 옛 문구(EX형)",
     "f3 3c ec 3d f1 ef ed ae f0 79 f3 d9 ed 2c f1 fa"),
    # SLPS_020.70 에만 남아 있던 원문 그대로의 개조 확인 메시지(감사기 사각지대)
    ("개조 확인 메시지 원문 잔재",
     "4b 3a 6a 69 89 7d 58 e4 f6 87 8c 56 43 66 58 4a 14"),
]


def _find_all(buf, pat):
    out, i = [], 0
    while True:
        i = buf.find(pat, i)
        if i < 0:
            return out
        out.append(i)
        i += 1


def run_metrics(buf, s, e):
    """[s, e) 구간의 (advance, 끝 phase). 제어코드는 건너뛴다."""
    p, adv, ph = s, 0, 0
    while p < e:
        b = buf[p]
        if b < 0xEB:
            idx, n = b, 1
        elif b <= 0xF5:
            idx, n = ((b - 0xEB) << 8) | buf[p + 1], 2
        else:
            p += 1
            continue
        st, ph = glyph_advance(idx, ph)
        adv += st
        p += n
    return adv, ph


def runs_of(buf, anchor, end, back=0, skip_if_in=None):
    out = []
    for h in _find_all(buf, anchor):
        s = h + len(anchor) - back
        e = buf.find(end, s)
        if e < 0:
            continue
        if skip_if_in is not None and buf[s:e] in skip_if_in:
            continue                    # 재배치로 남은 레트일 죽은 사본
        out.append(run_metrics(buf, s, e))
    return sorted(out)


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
        for name, anchor_hex, end_hex, back in RUNS:
            anchor = bytes.fromhex(anchor_hex.replace(" ", ""))
            end = bytes.fromhex(end_hex.replace(" ", ""))
            want = runs_of(jp, anchor, end, back)
            got = runs_of(ko, anchor, end, back, skip_if_in=jp)
            if want != got:
                print(f"  [실패] {rel} {name}: (칸,반칸) {got} — 레트일은 {want}")
                bad += 1
            else:
                print(f"  {rel:20} {name}: {got} (레트일과 동일)")
        for name, pat_hex in FORBIDDEN:
            pat = bytes.fromhex(pat_hex.replace(" ", ""))
            n = len(_find_all(ko, pat))
            if n:
                print(f"  [실패] {rel} '{name}' 이 아직 {n}곳 남았다")
                bad += 1

    if bad:
        raise SystemExit(f"UI 런 폭·반칸 검증 실패 {bad}건")
    print("UI 런 폭·반칸 검증 통과")


if __name__ == "__main__":
    main()
