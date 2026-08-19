# -*- coding: utf-8 -*-
"""대사에서 **화자 이름이 통째로 사라진 레코드**를 잡는다 (build_all 8단계).

`tools/second_translation_codec.py` 의 `SQUEEZE_LADDER` 마지막 단계는 대사가
상자에 안 들어갈 때 여는 따옴표 앞을 잘라낸다 — 즉 **화자 이름을 지운다**.

    for _open in ("「", "("):
        k = out.find(_open)
        if 0 < k <= 12:
            out = out[k:]

바이트는 줄지만 화면에서는 누가 말하는지 알 수 없게 되고, 대개 그 앞 단계까지
같이 걸려 띄어쓰기도 전부 사라진다. 2026-08-19 전수 조사에서 12곳이 이 상태였다
(제2차 1 · 제3차 4 · EX 7 — 시로코·메키보스·코우지·시냅스·세니아·얀롱·카크스·
신고·슈우×2·루오조르). 전부 번역문을 다시 써서 화자를 살렸다.

여기서는 **레트일이 `이름「` 로 시작하는데 배포본이 `「` 로 시작하는** 레코드를
센다. 0이 아니면 빌드를 세운다 — 번역을 줄여서 넣어야 한다는 뜻이다.
"""
import os
import re
import sys
from pathlib import Path

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools", "image-build", "audit"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P                                # noqa: E402
import assemble_image as AI                             # noqa: E402
import audit_all as A                                   # noqa: E402
from analyze_sce_relocation import parse_scenarios      # noqa: E402

GAMES = [("제2차", "SECOND/2_SCE.BIN"), ("제3차", "THIRD/3_SCE.BIN"), ("EX", "EX/E_SCE.BIN")]
_CTRL = re.compile(r"\[[0-9A-F]{2}( [0-9a-f]{2})*\]")
_SPEAKER = re.compile(r"^([^「]{1,12})「")


def _decode(buf, s, table, limit=200):
    out, p, n = [], s, 0
    while p < len(buf) and n < limit:
        b = buf[p]
        if b == 0xFF:
            break
        if b < 0xEB:
            out.append(table.get(b, "")); p += 1
        elif b <= 0xF5:
            out.append(table.get(((b - 0xEB) << 8) | buf[p + 1], "")); p += 2
        else:
            p += 1 + A.ARG.get(b, 0)
        n += 1
    return "".join(out)


def scan(ko: bytes, jp: bytes, ko_tab, jp_tab):
    """[(레트일 오프셋, 배포본 오프셋, 화자, 원문 앞부분, 한글 앞부분)]"""
    out = []
    for a, b in zip(parse_scenarios(jp), parse_scenarios(ko)):
        if len(a.records) != len(b.records):
            continue
        for ra, rb in zip(a.records, b.records):
            tj = _decode(jp, ra.start, jp_tab)
            m = _SPEAKER.match(tj)
            if not m:
                continue
            tk = _decode(ko, rb.start, ko_tab)
            if tk.startswith("「"):
                out.append((ra.start, rb.start, m.group(1), tj[:36], tk[:36]))
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

    ko_tab = A.ko_table(A.PINNED)
    jp_tab = {i: c for i, c in A.JP.items() if c}

    with AI.RawMode2Image(img) as m:
        _, entries = AI.read_tree(m)
    by = {e.path.strip("/"): e for e in entries}

    bad = 0
    for tag, rel in GAMES:
        e = by.get(rel)
        if e is None:
            continue
        ko = AI.read_file(img, e.lba, e.size)
        jp = (_P.EXTRACTED / rel).read_bytes()
        rows = scan(ko, jp, ko_tab, jp_tab)
        if rows:
            print(f"  [실패] {tag}: 화자가 지워진 대사 {len(rows)}건")
            for r in rows[:8]:
                print(f"     0x{r[0]:x} {r[2]}: {r[3]} -> {r[4]}")
            bad += len(rows)
        else:
            print(f"  {tag:5} 화자 지워진 대사 0건")
    if bad:
        raise SystemExit(
            f"화자 검증 실패 {bad}건 — 번역이 상자를 넘쳐 축약 사다리가 화자 이름을 "
            f"지웠습니다. 해당 대사를 짧게 다시 쓰세요")
    print("화자 검증 통과")


if __name__ == "__main__":
    main()
