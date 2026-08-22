#!/usr/bin/env python3
"""게이트: 인명·기체명·무기명이 세 게임에서 같은가.

같은 레트일 일본어 레코드가 제2차·제3차·EX 의 같은 표에 있으면 한국어도 같아야 한다.
따로 만든 세 파이프라인(제2차 오버레이 / 제3차 jp2ko / EX jp2ko)이 갈라지면 여기서 잡힌다.
무기명은 추가로 공백이 하나도 없어야 한다(사용자 지시 2026-08-22).
"""
from __future__ import annotations
import json, struct, sys
from pathlib import Path

_R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_R / "image-build")); sys.path.insert(0, str(_R / "tools"))
import assemble_image as AI                                   # noqa: E402
from second_translation_codec import load_safe_glyph_map      # noqa: E402

# 표 = (실행파일, 포인터배열 시작 오프셋, 항목수).  오프셋은 필드상대 s32 를 쓴다.
TABLES = {
    "제2차": ("SECOND/SECOND.WAR", {"무기": (0xC6BC, 352), "짧은이름": (0x10CE10, 400),
                                    "전체이름": (0x10DD68, 400), "기체": (0x10F47C, 448)}),
    "제3차": ("THIRD/THIRD.WAR",  {"무기": (0xC9B0, 352), "짧은이름": (0x10DBFC, 400),
                                    "전체이름": (0x10EB30, 400), "기체": (0x11020C, 448)}),
    "EX":    ("EX/EX.WAR",        {"무기": (0xCBD0, 352), "짧은이름": (0x107790, 400),
                                    "전체이름": (0x1081C0, 400), "기체": (0x108F68, 448)}),
}


def _decode(buf: bytes, start: int, table: dict[int, str]) -> str:
    out, p = [], start
    while p < len(buf) and buf[p] != 0xFF:
        b = buf[p]
        if b < 0xEB:
            idx, n = b, 1
        elif b <= 0xF5:
            if p + 1 >= len(buf):
                break
            idx, n = ((b - 0xEB) << 8) | buf[p + 1], 2
        else:
            break
        out.append(table.get(idx, "")); p += n
    return "".join(out)


def _read_tables(image: Path, extracted: Path) -> dict:
    jp_map = json.loads((extracted.parent / "research" /
                         "srwcb_embedded_font_mapping_reviewed.json").read_text("utf-8"))
    jp = {r["glyph_index"]: (r.get("character") or "") for r in jp_map["rows"]}
    ko = {v: k for k, v in load_safe_glyph_map().items()}
    with AI.RawMode2Image(image) as img:
        _, entries = AI.read_tree(img)
    by = {e.path.strip("/"): e for e in entries}
    result: dict = {}
    for game, (rel, tabs) in TABLES.items():
        if rel not in by:
            raise SystemExit(f"이미지에 {rel} 이 없다")
        patched = AI.read_file(image, by[rel].lba, by[rel].size)
        retail = (extracted / rel).read_bytes()
        for name, (base, count) in tabs.items():
            rows = {}
            for i in range(count):
                field = base + 4 * i
                if field + 4 > len(patched):
                    break
                tk = field + struct.unpack_from("<i", patched, field)[0]
                tj = field + struct.unpack_from("<i", retail, field)[0]
                if not (0x800 <= tk < len(patched) and 0x800 <= tj < len(retail)):
                    continue
                src = _decode(retail, tj, jp)
                if src:
                    rows[src] = _decode(patched, tk, ko)
            result[(game, name)] = rows
    return result


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--extracted", type=Path, required=True)
    a = ap.parse_args()

    tabs = _read_tables(a.image, a.extracted)
    failures = 0
    for name in ("무기", "짧은이름", "전체이름", "기체"):
        merged: dict[str, dict[str, str]] = {}
        for game in TABLES:
            for src, dst in tabs[(game, name)].items():
                merged.setdefault(src, {})[game] = dst
        split = {s: v for s, v in merged.items()
                 if len(v) >= 2 and len(set(v.values())) > 1}
        print(f"  {name:6} 레코드 {len(merged):4}  게임간 불일치 {len(split)}")
        for src, v in sorted(split.items())[:12]:
            print("      " + src + "  ->  " +
                  " | ".join(f"{g}:{v[g]}" for g in TABLES if g in v))
        failures += len(split)

    spaced = [(g, s, d) for g in TABLES
              for s, d in tabs[(g, "무기")].items() if " " in d or "\u3000" in d]
    print(f"  무기명 공백 포함 {len(spaced)}")
    for g, s, d in spaced[:12]:
        print(f"      {g} {s} -> {d}")
    failures += len(spaced)

    if failures:
        print(f"FAIL 용어 통일 위반 {failures}건")
        return 1
    print("PASS 인명·기체명·무기명이 세 게임에서 동일하고 무기명에 공백이 없다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
