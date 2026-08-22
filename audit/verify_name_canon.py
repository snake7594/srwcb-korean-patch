#!/usr/bin/env python3
"""게이트: 용어가 네 실행파일에서 같은가.

한 디스크에 제2차·제3차·EX·트레이닝(TR) 이 들어 있고 넷이 같은 구조의 UI 표를
각자 하나씩 갖는다. **같은 레트일 일본어 레코드는 네 곳에서 같은 한국어**여야 한다.
세 파이프라인(제2차 오버레이 / 제3차·EX·TR 의 jp2ko + 공백제거 사전)이 갈라지면
여기서 잡힌다.

무기명은 추가로 공백이 하나도 없어야 한다(사용자 지시 2026-08-22).
"""
from __future__ import annotations
import json, struct, sys
from pathlib import Path

_R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_R / "image-build")); sys.path.insert(0, str(_R / "tools"))
import assemble_image as AI                                   # noqa: E402
from second_translation_codec import load_safe_glyph_map      # noqa: E402

# 표 = (포인터배열 시작 오프셋, 항목수). 오프셋은 필드상대 s32 를 쓴다.
# 주입기(tools/patch_second_exe_ui.py · third-ui · ex-ui · tr-ui)의 표 정의와 같다.
TABLES: dict[str, tuple[str, dict[str, tuple[int, int]]]] = {
    "제2차": ("SECOND/SECOND.WAR", {
        "terrain_names": (0xB834, 144), "spirit_commands": (0xBC78, 94),
        "enhancement_parts": (0xC344, 64), "weapon_names": (0xC6BC, 1408),
        "pilot_skills": (0x11024, 52), "unit_abilities": (0x112BC, 22),
        "scenario_titles": (0x113C8, 192), "pilot_short_names": (0x10CE10, 400),
        "pilot_full_names": (0x10DD68, 400), "unit_names": (0x10F47C, 448)}),
    "제3차": ("THIRD/THIRD.WAR", {
        "terrain_names": (0xBB10, 144), "spirit_commands": (0xBF6C, 94),
        "enhancement_parts": (0xC638, 64), "weapon_names": (0xC9B0, 1408),
        "pilot_skills": (0x11310, 52), "unit_abilities": (0x11560, 22),
        "scenario_titles": (0x1166C, 192), "pilot_short_names": (0x10DBFC, 400),
        "pilot_full_names": (0x10EB30, 400), "unit_names": (0x11020C, 448)}),
    "EX": ("EX/EX.WAR", {
        "terrain_names": (0xBCB8, 144), "spirit_commands": (0xC188, 94),
        "enhancement_parts": (0xC854, 64), "weapon_names": (0xCBD0, 1344),
        "pilot_skills": (0xF25C, 52), "unit_abilities": (0xF514, 22),
        "scenario_titles": (0xF620, 192), "pilot_short_names": (0x107790, 400),
        "pilot_full_names": (0x1081C0, 400), "unit_names": (0x108F68, 448)}),
    "TR": ("TR.WAR", {
        "terrain_names": (0xBCB0, 144), "spirit_commands": (0xC180, 94),
        "enhancement_parts": (0xC84C, 64), "weapon_names": (0xCBC8, 1344),
        "pilot_skills": (0xF254, 52), "unit_abilities": (0xF50C, 22),
        "scenario_titles": (0xF618, 192), "pilot_short_names": (0x10776C, 400),
        "pilot_full_names": (0x10819C, 400), "unit_names": (0x108F44, 448)}),
}
ASSETS = list(TABLES["제2차"][1])


def _decode(buf: bytes, start: int, table: dict[int, str]) -> str | None:
    """레코드 하나를 글리프 표로 푼다. 제어바이트나 미매핑 글리프가 나오면 None."""
    out, p = [], start
    while p < len(buf) and buf[p] != 0xFF:
        b = buf[p]
        if b < 0xEB:
            idx, n = b, 1
        elif b <= 0xF5:
            if p + 1 >= len(buf):
                return None
            idx, n = ((b - 0xEB) << 8) | buf[p + 1], 2
        else:
            return None
        ch = table.get(idx)
        if ch is None:
            return None
        out.append(ch); p += n
    return "".join(out)


def read_tables(image: Path, extracted: Path) -> dict:
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
        for asset, (base, count) in tabs.items():
            rows: dict[str, set[str]] = {}
            for i in range(count):
                field = base + 4 * i
                if field + 4 > len(patched) or field + 4 > len(retail):
                    break
                tk = field + struct.unpack_from("<i", patched, field)[0]
                tj = field + struct.unpack_from("<i", retail, field)[0]
                if not (0x800 <= tk < len(patched) and 0x800 <= tj < len(retail)):
                    continue
                src = _decode(retail, tj, jp)
                dst = _decode(patched, tk, ko)
                if src and dst is not None:
                    rows.setdefault(src, set()).add(dst)
            result[(game, asset)] = rows
    return result


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("image", type=Path)
    ap.add_argument("--extracted", type=Path, required=True)
    ap.add_argument("--show", type=int, default=8, help="자산별로 보여 줄 위반 개수")
    a = ap.parse_args()

    tabs = read_tables(a.image, a.extracted)
    failures = 0
    for asset in ASSETS:
        merged: dict[str, dict[str, set[str]]] = {}
        for game in TABLES:
            for src, dsts in tabs[(game, asset)].items():
                merged.setdefault(src, {}).setdefault(game, set()).update(dsts)
        split = {s: v for s, v in merged.items()
                 if len(v) >= 2 and len({d for ds in v.values() for d in ds}) > 1}
        # 한 게임 안에서 같은 일본어가 두 한국어로 나오는 것도 불일치다.
        inner = {s: v for s, v in merged.items()
                 if s not in split and any(len(ds) > 1 for ds in v.values())}
        mark = "" if not (split or inner) else "  <-"
        print(f"  {asset:20} 레코드 {len(merged):5}  게임간 {len(split):4}  게임내 {len(inner):3}{mark}")
        for src, v in list(sorted(split.items()))[:a.show]:
            print("      " + src + "  ->  " +
                  " | ".join(f"{g}:{'/'.join(sorted(v[g]))}" for g in TABLES if g in v))
        for src, v in list(sorted(inner.items()))[:a.show]:
            print("      (게임내) " + src + "  ->  " +
                  " | ".join(f"{g}:{'/'.join(sorted(v[g]))}" for g in TABLES if g in v))
        failures += len(split) + len(inner)

    spaced = [(g, s, d) for g in TABLES
              for s, ds in tabs[(g, "weapon_names")].items() for d in ds
              if " " in d or "\u3000" in d]
    print(f"  {'무기명 공백':20} {len(spaced)}")
    for g, s, d in spaced[:a.show]:
        print(f"      {g} {s} -> {d}")
    failures += len(spaced)

    if failures:
        print(f"FAIL 용어 통일 위반 {failures}건")
        return 1
    print("PASS 네 실행파일(제2차·제3차·EX·TR)의 표 10종이 같은 용어를 쓰고 "
          "무기명에 공백이 없다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
