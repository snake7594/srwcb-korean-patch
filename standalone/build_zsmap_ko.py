# -*- coding: utf-8 -*-
"""제2차 단독판 `Z_SMAP.BIN` 을 한글 타이틀 메뉴·프롤로그가 든 판으로 만든다.

CB 의 `C_SMAP.BIN` 과 **같은 그래픽인데 파일 오프셋만 다르다**. 그래서 픽셀
위치를 박아 두지 않고 TIM 헤더의 VRAM 좌표로 찾는다(`tools/graphics/smap_ko.py`).

    타이틀 메뉴     멤버 25 전체           단독판 0xCD391
    오프닝 프롤로그  멤버 34 의 두 번째 스트림  단독판 0x14A8B0

CB 에만 있는 '게임 선택' 메뉴(멤버 21)는 단독판에 없다 — 넣지 않는다.
크기가 변하지 않으므로 ISO 에서 원래 자리에 그대로 덮어쓴다.
"""
import os
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
import srwcb_paths as _P  # noqa: E402

sys.path.insert(0, os.path.join(_d, "tools", "graphics"))
sys.path.insert(0, os.path.join(_d, "image-build"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import smap_ko  # noqa: E402
import assemble_image as AI  # noqa: E402

MENU_AT = 0xCD391
PROLOGUE_AT = 0x14A8B0
NAME = "Z_SMAP.BIN"


def retail_zsmap():
    """단독판 디스크에서 Z_SMAP.BIN 을 뽑는다(있으면 재사용)."""
    cached = config.SRW2_EXTRACTED / NAME
    if cached.exists():
        return cached.read_bytes()
    img = config.SRW2_IMG
    with AI.RawMode2Image(img) as m:
        _, entries = AI.read_tree(m)
    for e in entries:
        if e.path.strip("/") == NAME:
            data = AI.read_file(img, e.lba, e.size)
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_bytes(data)
            return data
    raise SystemExit(f"[없음] 단독판 디스크에 {NAME} 이 없습니다")


def main():
    src = retail_zsmap()
    out = smap_ko.patch_smap(src, MENU_AT, PROLOGUE_AT)
    assert len(out) == len(src), "크기 변동 — 제자리 교체가 안 된다"
    dst = _P.BUILD / "gfx" / "Z_SMAP_ko.BIN"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(out)
    print(f"WROTE {dst} ({len(out):,}B, 원본과 같은 크기)")


if __name__ == "__main__":
    main()
