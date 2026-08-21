# -*- coding: utf-8 -*-
"""MODE2 서브모드 EOF 비트를 검사한다 (build_all 8단계).

## 왜 중요한가

MODE2 섹터의 8바이트 서브헤더 중 셋째 바이트가 **서브모드**이고, 비트7(0x80)이
**EOF**(파일 마지막 섹터)다. 레트일은 데이터 파일 전 섹터를 `0x08` 로 두고
**마지막 섹터에만 `0x89`** 를 찍는다 — 전 파일 예외 없이.

    레트일 서브헤더:  00 00 08 00 00 00 08 00   (마지막 섹터만 ...89...)

`CdRead` 는 EOF 섹터를 만나면 **거기서 전송을 끝낸다.** 그래서

* **재배치된 파일**이 남의 옛 자리를 쓰면 그 파일이 찍어 둔 EOF 를 물려받아,
  파일 한가운데에서 읽기가 끊긴다.
* **새로 이어붙인 꼬리**에는 EOF 가 아예 없어 읽기가 파일 끝에서 안 멈춘다.

2026-08-21, EX 전투 후 "커서가 없고 입력이 안 먹는" 정지를 세이브스테이트로
확정했을 때의 원인이 이것이다. `EX/E_SCE.BIN` 이 **2번째 섹터**에 EOF 를 물려받아
(옛 `SECOND/2_DEAD.BIN` 의 마지막 섹터) 시나리오가 앞부분만 올라왔고, 이벤트
스크립트 포인터가 채워지지 않은 빈 힙으로 굴러떨어져 인터프리터가 0 만 읽으며
매 프레임 양보만 반복했다. 메인 루프는 살아 있어 음악은 계속 났다.

## 판정

레트일에서 위반이 **0** 이므로 이건 진짜 엔진 규약이다. 배포본도 0 이어야 한다.
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

import srwcb_paths as _P                    # noqa: E402
import assemble_image as AI                 # noqa: E402

SEC = 2352
SUBMODE = 0x12
EOF_BIT = 0x80
#: Form 2(스트리밍) 섹터는 규약이 다르다 — 우리가 건드리지 않는 파일들이다.
FORM2_BIT = 0x20

#: 규약에서 뺄 파일. `NULL.DA` 는 디스크 꼬리를 메우는 더미라 게임이 읽지 않고,
#: **레트일 단독판에서도 EOF 가 아예 없다**(즉 원래 규약 대상이 아니다).
#: 게다가 우리 단독판은 재배치된 파일을 그 익스텐트 안에 넣으므로, 그 파일들의
#: EOF 가 NULL.DA 것으로 잡힌다.
SKIP = ("NULL.DA",)


def violations(img: Path):
    """[(경로, 섹터수, EOF 가 선 섹터 목록)] — 마지막 섹터 하나만 서 있어야 한다."""
    with AI.RawMode2Image(img) as m:
        _, entries = AI.read_tree(m)
    size = img.stat().st_size
    out = []
    with open(img, "rb") as f:
        for e in sorted((x for x in entries if not x.is_dir), key=lambda x: x.lba):
            if e.path.strip("/").split("/")[-1].upper() in SKIP:
                continue
            n = (e.size + 2047) // 2048
            if (e.lba + n) * SEC > size:
                continue                    # 이미지 끝을 넘는 더미
            eofs, form2 = [], False
            for k in range(n):
                f.seek((e.lba + k) * SEC + SUBMODE)
                sm = f.read(1)[0]
                if sm & FORM2_BIT:
                    form2 = True
                    break
                if sm & EOF_BIT:
                    eofs.append(k)
            if form2:
                continue
            if eofs != [n - 1]:
                out.append((e.path.strip("/"), n, eofs))
    return out


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v0.11.0")
    ap.add_argument("--image", type=Path)
    ap.add_argument("--standalone", action="store_true")
    a = ap.parse_args()
    targets = []
    img = a.image or (_P.OUT /
                      f"Super Robot Taisen Complete Box Korean {a.version} (Track 1).bin")
    if not img.exists():
        raise SystemExit(f"[없음] 이미지: {img}")
    targets.append(("컴플리트 박스", img))
    if a.standalone:
        for label, p in (
            ("제2차 단독판", _P.WORK / "srw2" / "port" / "Super Robot Taisen 2 (Korean).img"),
            ("제3차 단독판", _P.WORK / "srw3" / "port" / "Dai 3 Ji Super Robot Taisen (Korean).bin"),
            ("EX 단독판", _P.WORK / "srwex" / "port" / "Super Robot Taisen EX (Korean).img"),
        ):
            if p.exists():
                targets.append((label, p))
    bad = 0
    for label, p in targets:
        v = violations(p)
        for path, n, eofs in v:
            where = "없음" if not eofs else str(eofs[:6])
            print(f"  [실패] {label} {path}: 섹터 {n}, EOF 위치 {where} (기대 [{n - 1}])")
        bad += len(v)
        if not v:
            print(f"  {label}: 서브모드 EOF 규약 준수")
    if bad:
        raise SystemExit(f"서브모드 EOF 검증 실패 {bad}건")
    print("서브모드 EOF 검증 통과")


if __name__ == "__main__":
    main()
