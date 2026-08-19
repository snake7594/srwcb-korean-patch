# -*- coding: utf-8 -*-
"""시나리오 예고 타이틀 카드 그래픽 검증.

확인하는 것
  1. 완성 이미지 4종(컴플리트 박스 + 단독판 3종)에 **한글 EFFECT.BIN** 이 들어갔다.
  2. 파일 크기가 레트일과 같다(제자리 교체).
  3. 손댄 멤버 9개의 **레코드 표가 레트일과 완전히 같다** — 표를 침범하면
     그 뒤 스프라이트가 통째로 어긋난다.
  4. 제목 스트립 픽셀이 실제로 바뀌었다(= 일본어가 남아 있지 않다).
  5. **구조 불변식** — 레코드 오프셋 기준점이 맞는지. 어긋나면 모든 스프라이트를
     같은 만큼 밀려 읽고 쓴다(v0.11.33 까지 2바이트=4픽셀 밀려 있었다).
  6. **변경 바이트 봉쇄** — 레트일과 다른 바이트가 전부 '바꾸기로 한 레코드'
     안에 있다. 밀려 쓰면 늘 다음 레코드(영문 부제·작은 숫자) 앞 4픽셀을
     덮어쓰는데, 5번만으로는 그걸 못 잡는다.
"""
import hashlib
import os
import sys
from pathlib import Path

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "image-build", "tools/graphics"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P            # noqa: E402
import assemble_image as AI         # noqa: E402
import eyecatch as EC               # noqa: E402
import build_effect_ko as B         # noqa: E402
from srw_lz_fast import decompress  # noqa: E402


def effect_of(img: Path) -> bytes:
    with AI.RawMode2Image(img) as m:
        _, entries = AI.read_tree(m)
    e = next(x for x in entries if x.path.strip("/") == "EFFECT.BIN")
    return AI.read_file(img, e.lba, e.size)


def images(cb_only=False):
    # 버전 문자열은 사전순이 아니다(v0.11.9 > v0.11.22). 최신 파일로 고른다.
    def newest(g):
        return sorted(g, key=lambda q: q.stat().st_mtime)[-1:]
    out = [("컴플리트 박스", newest(_P.OUT.glob("*Korean*(Track 1).bin")))]
    if cb_only:
        return [(n, p[0]) for n, p in out if p]
    for key, pat in (("제2차 단독판", "srw2/port/*Korean*.img"),
                     ("제3차 단독판", "srw3/port/*Korean*.bin"),
                     ("EX 단독판", "srwex/port/*Korean*.img")):
        out.append((key, newest(_P.WORK.glob(pat))))
    return [(n, p[0]) for n, p in out if p]


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    # 단독판은 build_all 9단계에서 만들어진다. 8단계에서 검사하면 아직 옛
    # 이미지라 애먼 실패가 나고, 그 바람에 9단계가 아예 안 돈다.
    ap.add_argument("--cb-only", action="store_true",
                    help="컴플리트 박스만 검사 (단독판 빌드 전)")
    args = ap.parse_args()
    retail = (_P.EXTRACTED / "EFFECT.BIN").read_bytes()
    ko = (_P.BUILD / "gfx" / "EFFECT_ko.BIN").read_bytes()
    want = hashlib.sha256(ko).hexdigest()
    ms = B.members(retail)
    bad = 0

    # 멤버별 레코드 표 + 픽셀 변경 확인
    for mi, (pos, span) in B.MEMBERS.items():
        limit = next(e for s, e in ms if s <= pos < e)
        r0 = decompress(retail[pos:limit], 0)[0]
        r1 = decompress(ko[pos:limit], 0)[0]
        a, _ = EC.parse(r0)
        b, _ = EC.parse(r1)
        if a != b:
            print(f"  [실패] m{mi}: 레코드 표가 다르다")
            bad += 1
            continue
        ss = B.strips(a, span)
        same = [r["i"] for r in ss
                if r0[r["off"]:r["off"] + r["w"] * r["h"] // 2]
                == r1[r["off"]:r["off"] + r["w"] * r["h"] // 2]]
        if same:
            print(f"  [실패] m{mi}: 안 바뀐 제목 스트립 {same}")
            bad += 1

        # (5) 구조 불변식 — 기준점이 맞아야만 성립한다
        for lbl, raw, recs, hdr_end in (("레트일", r0, a, EC.parse(r0)[1]),
                                        ("한글", r1, b, EC.parse(r1)[1])):
            last = recs[-1]
            end = last["off"] + last["w"] * last["h"] // 2
            slack = len(raw) - end
            gap = recs[0]["off"] - hdr_end
            if not (0 <= slack <= 1):
                print(f"  [실패] m{mi} {lbl}: 픽셀 끝 0x{end:X} 과 블롭 길이"
                      f" 0x{len(raw):X} 차이 {slack} — 레코드 기준점이 틀렸다")
                bad += 1
            if gap < 4 or (gap - 4) % 32:
                print(f"  [실패] m{mi} {lbl}: 레코드표~첫 픽셀 간격 {gap} 이"
                      f" 4+32N 이 아니다 — 레코드 기준점이 틀렸다")
                bad += 1

        # (6) 변경 바이트 봉쇄 — 바꾸기로 한 레코드 밖은 한 바이트도 달라선 안 된다
        allowed = []
        for r in list(ss) + [a[EC.DAI_INDEX], a[EC.WA_INDEX]]:
            allowed.append((r["off"], r["off"] + r["w"] * r["h"] // 2))
        allowed.sort()
        stray = 0
        for off in range(min(len(r0), len(r1))):
            if r0[off] == r1[off]:
                continue
            if not any(lo <= off < hi for lo, hi in allowed):
                stray += 1
        if stray:
            print(f"  [실패] m{mi}: 바꾸기로 한 레코드 **밖**에서 {stray}바이트가"
                  f" 달라졌다 — 이웃 레코드를 침범했다")
            bad += 1
    print(f"멤버 {len(B.MEMBERS)}개: 레코드 표 동일, 제목 스트립 전부 교체" if not bad else "")

    for name, img in images(args.cb_only):
        d = effect_of(img)
        got = hashlib.sha256(d).hexdigest()
        ok = (got == want) and len(d) == len(retail)
        print(f"  {name:<12} {img.name}: {'OK' if ok else '실패'} "
              f"({len(d):,}B, sha {got[:12]})")
        if not ok:
            bad += 1

    if bad:
        raise SystemExit(f"예고 타이틀 카드 검증 실패 {bad}건")
    print("예고 타이틀 카드 검증 통과")


if __name__ == "__main__":
    main()
