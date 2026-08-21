# -*- coding: utf-8 -*-
"""대사 **한 페이지**가 128바이트 줄버퍼를 넘지 않는지 본다 (build_all 8단계).

## 왜

대사 한 페이지를 조립하는 루틴(EX `0x800C4F04`)은 호출자 `0x800C4D14` 의 스택
프레임 안 **128바이트 로컬 버퍼**(sp+24)에 한 바이트씩 써 넣는데 **길이 검사가 없다.**
같은 프레임의 sp+152/156 에는 호출자가 저장해 둔 **s0·s1** 이 있다.

    0x800C4D14  addiu sp,sp,-168     sp = 0x801FFED8
                줄버퍼  = sp+24     = 0x801FFEF0   (128바이트)
                sw s0,152(sp)       = 0x801FFF70
                sw s1,156(sp)       = 0x801FFF74

한 페이지가 128바이트를 넘으면 그 s0·s1 이 **글자 바이트로 덮인다.** 그런데 s0·s1 은
스크립트 인터프리터가 프롤로그에서 넣어 둔 **디스패치 표 주소**다.

    0x80062B48  addiu s1,v0,0x6A28   s1 = 0x80116A28 (옵코드표)
    0x80062B54  addiu s0,v0,0x474    s0 = 0x80010474 (카테고리표)
    0x80062B94  addu  v0,v0,s0 / lw v0,(v0) / jr v0

s0 이 깨지면 다음 디스패치가 미매핑 주소를 읽어 오픈버스 `0xFFFFFFFF` 를 얻고
**`jr 0xFFFFFFFF`** 로 뛴다 → AdEL 예외 → BIOS 미해결예외 루프(SR.IEc=0) →
인터럽트가 막혀 **RAM 전체가 얼어붙는 하드 행**.

2026-08-22, EX 23화 듀라크실(페일로드) 격파 직후 정지의 원인이 정확히 이것이었다.
페일 사망 대사 한 줄이 135바이트로 전개돼 7바이트를 넘겼고, 그 대사는 sc25·sc27·sc51
세 곳에 같은 바이트로 들어 있었다.

## 페이지 경계

`0x800C4F04` 는 **3번째 F6 / F7 / FF** 에서 끊고, `F6` 은 `F6 00` 2바이트로 전개한다.
그래서 세는 단위는 레코드 전체가 아니라 **페이지**다.

## 한도가 128 이 아니라 **127** 인 이유

버퍼는 128바이트(0x801FFEF0~0x801FFF6F)인데 조립 루틴은 페이지 뒤에 **종단자 1바이트**를
더 쓴다. 실측으로 확인했다 — 135바이트 페이지가 136바이트를 써서 0x801FFF70·0x801FFF74
의 s0·s1 **정확히 8바이트**를 덮었다(136 − 128 = 8). 따라서

    기록량 = 페이지 + 1 ≤ 128   =>   **페이지 ≤ 127**

## 판정

레트일 최대: EX 98 · 제3차 104 · 제2차 100 — **세 게임 모두 위반 0건**.
따라서 이건 진짜 엔진 한계이고, 배포본도 0건이어야 한다.
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

import srwcb_paths as _P                                # noqa: E402
import assemble_image as AI                             # noqa: E402
from analyze_sce_relocation import parse_scenarios      # noqa: E402

LIMIT = 127
CTRL_ARGS = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0,
             0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}
SCE = (("EX/E_SCE.BIN", "EX"), ("THIRD/3_SCE.BIN", "제3차"),
       ("SECOND/2_SCE.BIN", "제2차"))


def page_lengths(buf, start, end):
    """0x800C4F04 모형: 3번째 F6 / F7 / FF 에서 끊고 F6 은 2바이트로 전개."""
    out, n, f6, p = [], 0, 0, start
    while p < end:
        x = buf[p]
        if x == 0xFF:
            break
        if x == 0xF7:
            out.append(n)
            n = f6 = 0
            p += 1
            continue
        if x == 0xF6:
            f6 += 1
            n += 2
            p += 1
            if f6 == 3:
                out.append(n)
                n = f6 = 0
            continue
        if x < 0xEB:
            n, p = n + 1, p + 1
        elif x <= 0xF5:
            n, p = n + 2, p + 2
        else:
            k = 1 + CTRL_ARGS.get(x, 0)
            n, p = n + k, p + k
    out.append(n)
    return [v for v in out if v]


def dialogue_records(buf):
    """대사 포인터가 실제로 겨누는 레코드."""
    out = set()
    for s in parse_scenarios(buf):
        by_start = {r.start: r for r in s.records}
        for ref in s.references:
            r = by_start.get(ref.target)
            if r:
                out.add((r.start, r.end))
    return sorted(out)


def check(buf, label):
    bad = []
    worst = 0
    for a, b in dialogue_records(buf):
        for n in page_lengths(buf, a, b):
            worst = max(worst, n)
            if n > LIMIT:
                bad.append((n, a))
    return worst, bad


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
    total = 0
    for rel, label in SCE:
        if rel not in by:
            continue
        ko = AI.read_file(img, by[rel].lba, by[rel].size)
        jp = (_P.EXTRACTED / rel).read_bytes()
        jw, jb = check(jp, label)
        kw, kb = check(ko, label)
        if jb:
            print(f"  [실패] {label}: 레트일이 이미 {len(jb)}건 초과 — 한계값이 틀렸다")
            total += 1
            continue
        for n, off in sorted(kb, reverse=True):
            print(f"  [실패] {label} 0x{off:X}: 한 페이지 {n}바이트 "
                  f"(한도 {LIMIT}, {n - LIMIT}바이트 초과)")
        total += len(kb)
        if not kb:
            print(f"  {label:5} 페이지 최대 {kw:3}바이트 / 한도 {LIMIT}  (레트일 {jw})")
    if total:
        raise SystemExit(f"대사 페이지 길이 검증 실패 {total}건")
    print("대사 페이지 길이 검증 통과")


if __name__ == "__main__":
    main()
