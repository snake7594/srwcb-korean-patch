# -*- coding: utf-8 -*-
"""세이브/로드 화면 머리글이 레트일과 같은 자리에 앉는지 확인한다 (제보 #6).

화면은 이렇게 그려진다.

    … [FC 07 00] [F8 00]=`セ-ブデ-タ`(6칸) [FC dx 00] [F8 00]=`スロット1`

라벨 자리는 **6바이트 고정**이라 한국어로는 6칸을 만들 수 없다. 전각 글리프는
`1+phase` 칸씩 나아가서 (전각2+반각2)=5칸이 한계다. 그래서 두 가지를 함께 본다.

  1) **자리**  라벨 advance + 뒤따르는 커서 이동(dx) 이 레트일과 같아야 한다.
     안 맞으면 `슬롯1` 이 칸 경계에 걸친다.
  2) **phase** 라벨의 전각 글자 수가 짝수여야 한다(= phase 중립).
     phase 는 F6(줄바꿈)에서만 초기화되므로, 머리글이 뒤집어 놓으면 그 아래
     목록 **첫 줄만** 반 칸 밀린다(`자료01` 만 들여쓰기돼 보이던 증상).
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

import srwcb_paths as _P                       # noqa: E402
import assemble_image as AI                    # noqa: E402
from second_translation_codec import glyph_advance   # noqa: E402

#: 세이브/로드 머리글 골격. 가운데 dx 만 다를 수 있다.
_HEAD = (bytes.fromhex("fc 07 00 f8 00 fc"), bytes.fromhex("00 f8 00"))

#: 라벨 힙 레코드(레트일 바이트). 앞뒤 0xFF 로 레코드 경계까지 묶어 오탐을 막는다.
LABELS = {
    "セ-ブデ-タ": bytes.fromhex("AA 11 C5 B6 11 AE"),
    "ロ-ドデ-タ": bytes.fromhex("DC 11 B8 B6 11 AE"),
}

EXES = ["SECOND/SECOND.WAR", "THIRD/THIRD.WAR", "EX/EX.WAR", "TR.WAR"]


def run_advance(buf, s, e, phase=0):
    """[s,e) 를 렌더러가 나아가는 칸 수와 끝 phase."""
    adv, p = 0, s
    while p < e:
        b = buf[p]
        if b < 0xEB:
            idx, p = b, p + 1
        elif b <= 0xF5:
            idx, p = ((b - 0xEB) << 8) | buf[p + 1], p + 2
        else:
            raise ValueError(f"라벨 안에 제어코드 {b:02X}")
        step, phase = glyph_advance(idx, phase)
        adv += step
    return adv, phase


def anchor_dx(buf):
    """머리글 골격에서 라벨 뒤 커서 이동량(dx) 을 전부 모은다."""
    out, at = [], 0
    while True:
        at = buf.find(_HEAD[0], at)
        if at < 0:
            return out
        q = at + len(_HEAD[0])
        if buf[q + 1:q + 4] == _HEAD[1]:
            out.append(buf[q])
        at += 1


def check(name, ko, jp, log=print):
    bad = []
    dx_ko, dx_jp = set(anchor_dx(ko)), set(anchor_dx(jp))
    if not dx_jp:
        log(f"  {name}: 머리글 골격 없음 — 건너뜀")
        return bad
    if len(dx_ko) != 1 or len(dx_jp) != 1:
        bad.append(f"{name}: 커서 이동량이 여러 가지 ko={sorted(dx_ko)} jp={sorted(dx_jp)}")
        return bad
    dx_ko, dx_jp = dx_ko.pop(), dx_jp.pop()

    for jp_name, src in LABELS.items():
        at = jp.find(b"\xff" + src + b"\xff")
        if at < 0:
            log(f"  {name}: 라벨 `{jp_name}` 없음 — 건너뜀")
            continue
        s, e = at + 1, at + 1 + len(src)
        a_jp, _ = run_advance(jp, s, e)
        a_ko, ph = run_advance(ko, s, e)
        if a_ko + dx_ko != a_jp + dx_jp:
            bad.append(f"{name} `{jp_name}`: 슬롯 라벨이 레트일과 "
                       f"{a_ko + dx_ko - a_jp - dx_jp:+}칸 어긋남 "
                       f"(라벨 {a_ko}칸 + 이동 {dx_ko} vs 레트일 {a_jp}+{dx_jp})")
        if ph != 0:
            bad.append(f"{name} `{jp_name}`: 라벨이 phase 를 뒤집는다 — "
                       f"목록 첫 줄이 반 칸 밀린다 (전각 글자 수를 짝수로)")
        log(f"  {name:18} {jp_name}: 라벨 {a_ko}칸 + 이동 {dx_ko} = {a_ko + dx_ko} "
            f"(레트일 {a_jp + dx_jp}), 끝 phase {ph}")
    return bad


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v0.11.0")
    ap.add_argument("--image", type=Path)
    a = ap.parse_args()
    img = a.image or (_P.OUT /
                      f"Super Robot Taisen Complete Box Korean {a.version} (Track 1).bin")
    if not img.exists():
        raise SystemExit(f"[없음] 이미지: {img}")
    bad = []
    for rel in EXES:
        ko = AI_read(str(img), rel)
        jp = (_P.EXTRACTED / rel).read_bytes()
        bad += check(rel, ko, jp)
    if bad:
        for b in bad:
            print(f"  [실패] {b}")
        raise SystemExit(f"세이브 머리글 검증 실패 {len(bad)}건")
    print("세이브/로드 머리글 검증 통과")


def AI_read(img, rel, _cache={}):
    if img not in _cache:
        with AI.RawMode2Image(Path(img)) as m:
            _, entries = AI.read_tree(m)
        _cache[img] = {e.path.strip("/"): e for e in entries}
    e = _cache[img][rel]
    return AI.read_file(Path(img), e.lba, e.size)


if __name__ == "__main__":
    main()
