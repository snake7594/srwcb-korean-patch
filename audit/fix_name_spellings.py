# -*- coding: utf-8 -*-
"""이름 표기를 사전(`translation/glossary_names_ko.json`)에 맞춰 통일한다.

같은 인물·기체가 대사에서 여러 표기로 나오고 있었다(제보 2026-08-10:
'크와트로' 가 제3차 대사에선 전부 '콰트로'). 이름표(로스터)와도 어긋났다.

이 스크립트는 **번역 원본**을 고친다 — 대사 오버레이 3종과 이름표.
빌드 산출물이 아니라 원본을 고치므로 다시 빌드해도 유지된다.

    python audit/fix_name_spellings.py          # 확인만
    python audit/fix_name_spellings.py --write  # 실제로 고침
"""
import argparse
import json
import os
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
import srwcb_paths as _P    # noqa: E402
from pathlib import Path    # noqa: E402

GLOSSARY = _P.TRANSLATION / "glossary_names_ko.json"
OVERLAYS = [
    _P.TRANSLATION / "second_translation_overlay.json",
    _P.TRANSLATION / "third_translation_overlay.json",
    _P.REPO / "ex-ui" / "data" / "ex_translation_overlay.json",
]
NAMES = _P.TRANSLATION / "second_ui_names_overlay.json"
# 제3차 대사를 다른 경로로 재사용하는 사본
EXTRA = [_P.REPO / "ex-ui" / "data" / "third_dial_by_jp.json"]


def _apply(text, variants):
    n = 0
    for wrong, right in variants.items():
        if wrong in text:
            n += text.count(wrong)
            text = text.replace(wrong, right)
    return text, n


def fix_overlays(variants, write, log=print):
    total = 0
    for p in OVERLAYS:
        d = json.loads(p.read_text(encoding="utf-8"))
        n = 0
        for entry in d["translations"].values():
            parts = entry.get("ko_parts") or {}
            for k, v in list(parts.items()):
                if not isinstance(v, str):
                    continue
                new, c = _apply(v, variants)
                if c:
                    parts[k] = new
                    n += c
        if n:
            log(f"  {p.name}: {n}곳")
            if write:
                p.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
        total += n
    for p in EXTRA:
        if not p.exists():
            continue
        raw = p.read_text(encoding="utf-8")
        new, c = _apply(raw, variants)
        if c:
            log(f"  {p.name}: {c}곳")
            if write:
                p.write_text(new, encoding="utf-8")
            total += c
    return total


def fix_roster(roster_fixes, write, log=print):
    d = json.loads(NAMES.read_text(encoding="utf-8"))
    n = 0
    for tb in d["tables"]:
        for r in tb["rows"]:
            want = roster_fixes.get((r.get("japanese") or "").strip())
            if want and r.get("korean") != want:
                log(f"  이름표 {r['japanese']}: {r['korean']!r} -> {want!r}")
                r["korean"] = want
                n += 1
    if n and write:
        NAMES.write_text(json.dumps(d, ensure_ascii=False, indent=1) + "\n",
                         encoding="utf-8")
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="실제로 파일을 고친다")
    ap.add_argument("--check", action="store_true",
                    help="이형 표기가 남아 있으면 실패로 끝낸다 (빌드 게이트)")
    a = ap.parse_args()
    g = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    print("[대사 오버레이]")
    n1 = fix_overlays(g["variants"], a.write)
    print("[이름표]")
    n2 = fix_roster(g.get("roster_fixes", {}), a.write)
    print(f"\n대사 {n1}곳 / 이름표 {n2}곳" + ("" if a.write else "  (확인만 — 고치려면 --write)"))


if __name__ == "__main__":
    main()
