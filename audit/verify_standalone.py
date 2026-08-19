# -*- coding: utf-8 -*-
"""단독판(별매 CD) 이식 결과를 CB 와 같은 기준으로 검사한다.

단독판은 CB 빌드 결과를 오프셋만 바꿔 옮겨 심은 것이라, 아카이브 내용은 같아야
한다. 그래도 옮기다 어긋나면 화면에서만 드러나므로 같은 검사를 한 번 더 돌린다.
"""

# --- 이식용 부트스트랩 (자동 삽입): 저장소 어디서 실행하든 동작 ---
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.exists(_os.path.join(_d, "srwcb_paths.py")):
    _d = _os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import srwcb_paths as _P
_P.ensure_dirs()
for _sub in ("tools", "audit"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import sys

sys.path.insert(0, str(_P.TOOLS))
import audit_all as A  # noqa: E402
import verify_image as V  # noqa: E402
from extract_psx_iso import RawMode2Image, read_tree  # noqa: E402

GAMES = [
    ("제2차 단독판", _P.WORK / "srw2" / "port" / "Super Robot Taisen 2 (Korean).img",
     A.PINNED, ("2_SCE.BIN", "BMESS2.BIN", "2_DEAD.BIN"), "SECOND/2_SCE.BIN"),
    ("제3차 단독판", _P.WORK / "srw3" / "port" / "Dai 3 Ji Super Robot Taisen (Korean).bin",
     A.PINNED, ("3_SCE.BIN", "BMESS3.BIN", "3_DEAD.BIN"), "THIRD/3_SCE.BIN"),
    ("EX 단독판", _P.WORK / "srwex" / "port" / "Super Robot Taisen EX (Korean).img",
     A.EX15, ("E_SCE.BIN", "BMESS4.BIN", "E_DEAD.BIN"), "EX/E_SCE.BIN"),
]


def main() -> None:
    rows = []
    checked = 0
    for tag, img, extras, (sce, bm, dead), retail_sce in GAMES:
        if not img.exists():
            print(f"[건너뜀] {tag}: {img.name} 없음")
            continue
        checked += 1
        with RawMode2Image(img) as m:
            _, entries = read_tree(m)
        where = {e.path.strip("/").split("/")[-1]: e.path.strip("/") for e in entries}
        tbl = A.ko_table(extras)
        for name, kind, w, lines in ((sce, "대사", 0, 0),
                                     (bm, "전투", V.BATTLE_CAP_HALF, 0),
                                     (dead, "사망", V.BATTLE_CAP_HALF, 0)):
            d = A.read_iso(str(img), where[name])
            if kind == "대사":
                j = (_P.EXTRACTED / retail_sce).read_bytes()
                ko_recs, jp_recs = V._paired(d, j, tbl)
                V.check_pages(f"{tag} {kind}", d, j, ko_recs, jp_recs, tbl,
                              V.SCE_BOX_ADVANCE, rows)
                continue
            elif kind == "전투":
                recs = A.bmess_records(d)
            else:
                recs = V.dead_live(d)
            V.check(f"{tag} {kind}", d, recs, tbl, w, lines, rows)
    if not checked:
        raise SystemExit("검사할 단독판 이미지가 없습니다 (build_all.py --only 9)")
    print(f"\n{'항목':22} {'레코드':>8} {'미번역':>7} {'폭/줄초과':>9} {'깨짐':>6}")
    bad = 0
    for n, c, jp, wd, br in rows:
        print(f"{n:22} {c:>8,} {jp:>7} {wd:>9} {br:>6}")
        bad += jp + wd + br
    print()
    if bad:
        raise SystemExit(f"검증 실패: 문제 {bad}건")
    print("검증 통과: 미번역 0 / 폭·줄 초과 0 / 깨진 레코드 0")


if __name__ == "__main__":
    main()
