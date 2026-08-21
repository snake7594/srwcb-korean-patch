# -*- coding: utf-8 -*-
"""배포 이미지의 텍스트 VM 치환 패딩 루프가 전부 하한 검사를 갖췄는지 확인한다.

`F8 <인자>` 치환에서 정적 폭보다 런타임 값이 길면 부호 없는 카운트다운이
RAM 을 밀어 버려 그 자리에서 멈춘다. `audit/harden_text_vm.py` 가 그 분기를
`beqz` -> `blez` 로 바꾼다. 여기서는 **남아 있는 `beqz` 가 0곳**인지 본다.

레트일에는 실행파일마다 정확히 2곳 있다(핸들러가 `flags & 8` 로 두 갈래).
그러니 '0곳 발견' 은 통과가 아니라 **패턴을 못 찾았다는 뜻**일 수도 있어,
하드닝 전 원본에서 2곳이 나오는지도 같이 확인한다.
"""
import os
import sys
from pathlib import Path

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools", "image-build", "audit"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P                    # noqa: E402
import assemble_image as AI                 # noqa: E402
import harden_text_vm as HV                 # noqa: E402

#: 텍스트 VM 을 담은 오버레이. SLPS_020.70 은 런처라 VM 이 없다(패턴 0곳이 정상).
VM_EXES = ("SECOND/SECOND.WAR", "THIRD/THIRD.WAR", "EX/EX.WAR", "TR.WAR")
EXPECT = 2


def check_image(img: Path) -> int:
    with AI.RawMode2Image(img) as m:
        _, entries = AI.read_tree(m)
    by = {e.path.strip("/"): e for e in entries}
    bad = 0
    for rel in VM_EXES:
        e = by.get(rel)
        if e is None:
            continue
        ko = AI.read_file(img, e.lba, e.size)
        jp = (_P.EXTRACTED / rel).read_bytes()
        n_jp, n_ko = len(HV.find_sites(jp)), len(HV.find_sites(ko))
        if n_jp != EXPECT:
            print(f"  [실패] {rel}: 레트일에서 취약 자리가 {n_jp}곳 — 지문이 안 맞는다")
            bad += 1
        elif n_ko:
            print(f"  [실패] {rel}: 하한 검사 없는 치환 루프가 {n_ko}곳 남았다")
            bad += 1
        else:
            print(f"  {rel:20} 치환 패딩 루프 {n_jp}곳 모두 하한 검사 적용")
    return bad


def check_standalone() -> int:
    imgs = [
        ("제2차", _P.WORK / "srw2" / "port" / "Super Robot Taisen 2 (Korean).img",
         _P.WORK / "srw2" / "extracted"),
        ("제3차", _P.WORK / "srw3" / "port" / "Dai 3 Ji Super Robot Taisen (Korean).bin",
         _P.WORK / "srw3" / "extracted"),
        ("EX", _P.WORK / "srwex" / "port" / "Super Robot Taisen EX (Korean).img",
         _P.WORK / "srwex" / "extracted"),
    ]
    bad = 0
    for label, p, ext in imgs:
        if not p.exists():
            continue
        with AI.RawMode2Image(p) as m:
            _, entries = AI.read_tree(m)
        by = {e.path.strip("/"): e for e in entries}
        key = next((k for k in by if k.startswith("SLPS")), None)
        if key is None:
            continue
        n = len(HV.find_sites(AI.read_file(p, by[key].lba, by[key].size)))
        # '0곳' 이 '패턴을 못 찾음' 이 아니라 '전부 고쳐짐' 이라는 보증 —
        # 같은 지문이 레트일 실행파일에서는 정확히 EXPECT 곳 나와야 한다.
        retail = ext / key
        n_jp = len(HV.find_sites(retail.read_bytes())) if retail.exists() else None
        if n_jp is not None and n_jp != EXPECT:
            print(f"  [실패] {label} 단독판 {key}: 레트일에서 {n_jp}곳 — 지문이 안 맞는다")
            bad += 1
        elif n:
            print(f"  [실패] {label} 단독판 {key}: 하한 검사 없는 치환 루프 {n}곳")
            bad += 1
        else:
            base = f"(레트일 {n_jp}곳)" if n_jp is not None else "(레트일 원본 없음)"
            print(f"  {label} 단독판 {key}: 하한 검사 적용 {base}")
    return bad


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v0.11.0")
    ap.add_argument("--image", type=Path)
    ap.add_argument("--standalone", action="store_true")
    a = ap.parse_args()
    img = a.image or (_P.OUT /
                      f"Super Robot Taisen Complete Box Korean {a.version} (Track 1).bin")
    if not img.exists():
        raise SystemExit(f"[없음] 이미지: {img}")
    bad = check_image(img)
    if a.standalone:
        bad += check_standalone()
    if bad:
        raise SystemExit(f"VM 하한 검사 검증 실패 {bad}건")
    print("VM 치환 루프 하한 검사 검증 통과")


if __name__ == "__main__":
    main()
