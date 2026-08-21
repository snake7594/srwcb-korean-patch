# -*- coding: utf-8 -*-
"""전투 텍스트 조립 스크래치가 넘치지 않는지 확인한다 (build_all 8단계).

전투 메시지 평가기는 선택된 잎을 하나의 스크래치에 이어 붙인다. 잎 하나가
`화자 접두(FF 제외) + F6 + BMESS 레코드(FF 포함)` 이고 끝에 목록 종결 FF 가
하나 더 붙는다. 레트일은 모드 0~3 에 각각 0x100 바이트만 주고 **경계 검사가 없다**.
그 버퍼는 모듈 끝보다 뒤, 즉 malloc 힙 안이라 넘치면 맵·유닛 데이터가 덮인다.

한글은 전각(2바이트/글자)이라 같은 문장이 1.5배쯤 된다. 실측:

    제2차  레트일 257 -> 한글 366     슬롯 512 (확장 적용)
    제3차  레트일 261 -> 한글 382     슬롯 512 (v0.11.38 부터 확장)
    EX     레트일  89 -> 한글 126     슬롯 256 (여유)
    TR     레트일  89 -> 한글 126     슬롯 256 (여유)

여기서는 **배포본의 실제 슬롯 크기 이하**인지 본다. 레트일 값이 슬롯을 살짝
넘는 것은 평가기 모형이 보수적이라 그렇고(레트일이 실제로 깨지지는 않는다),
그래서 판정 기준은 레트일 대비가 아니라 **슬롯 크기 자체**다.
"""
import os
import struct
import sys
from pathlib import Path

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools", "image-build", "audit"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P                                        # noqa: E402
import assemble_image as AI                                     # noqa: E402
import expand_battle_scratch as BS                              # noqa: E402
from analyze_second_message_archives import (                   # noqa: E402
    analyze_bmess_runtime_scratch, parse_message_record)

BIAS = 0x8000F800
NAME_COUNT = 400

#: (실행파일, 전투 아카이브, 화자명표 파일오프셋, 표시이름)
#: 표는 실행파일마다 자기참조 400엔트리 표가 넷 있는데, 평가기가 쓰는 것은
#: 제2차 기준 0x10CE10 (tools/build_second_expanded_patch.py 가 하드코딩한 것)
#: 이고 나머지 셋은 같은 모양의 다른 표다. 오름차순 두 번째가 그것이다.
PAIRS = [
    ("SECOND/SECOND.WAR", "BMESS2.BIN", 0x10CE10, "제2차"),
    ("THIRD/THIRD.WAR", "BMESS3.BIN", 0x10DBFC, "제3차"),
    ("EX/EX.WAR", "BMESS4.BIN", 0x107790, "EX"),
    ("TR.WAR", "BMESS4.BIN", 0x10776C, "트레이닝"),
]


def prefix_lengths(buf, table):
    if struct.unpack_from("<I", buf, table - 4)[0] != BIAS + table:
        raise ValueError(f"화자명표 자기참조 헤더가 0x{table:X} 에서 안 맞는다")
    out = []
    for i in range(NAME_COUNT):
        field = table + i * 4
        target = field + struct.unpack_from("<i", buf, field)[0]
        record = parse_message_record(buf, target)
        out.append(record.end - record.start - 1)
    return tuple(out)


def check(img: Path) -> int:
    with AI.RawMode2Image(img) as m:
        _, entries = AI.read_tree(m)
    by = {e.path.strip("/"): e for e in entries}
    bad = 0
    for exe, arc, table, label in PAIRS:
        if exe not in by or arc not in by:
            continue
        ko = AI.read_file(img, by[exe].lba, by[exe].size)
        ka = AI.read_file(img, by[arc].lba, by[arc].size)
        slot = BS.slot_bytes(ko)
        used = analyze_bmess_runtime_scratch(ka, prefix_lengths(ko, table))["maximum_bytes"]
        if used > slot:
            print(f"  [실패] {label}: 전투 스크래치 최대 {used}B 가 슬롯 {slot}B 를 넘는다")
            bad += 1
        else:
            print(f"  {label:6} 전투 스크래치 최대 {used:4}B / 슬롯 {slot}B")
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
    if check(img):
        raise SystemExit("전투 스크래치 검증 실패")
    print("전투 스크래치 검증 통과")


if __name__ == "__main__":
    main()
