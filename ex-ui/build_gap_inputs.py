# -*- coding: utf-8 -*-
"""EX 보충 번역(build_ex_supplement)이 쓰는 두 입력을 재생성한다.

원래는 작업 중 만든 중간 산출물을 그대로 들고 있었는데, 그러면 그 파일이 없는
사람은 EX 를 빌드할 수 없다. 둘 다 **저장소 자산 + 사용자 디스크**로 다시 만들
수 있으므로 여기서 만든다.

  sce_gap_real.json    E_SCE 안에서 원장(ledger)이 다루지 않는 '갭' 텍스트 레코드
                       [{off, jp}] — E_SCE 를 훑어 텍스트 레코드를 찾고,
                       원장이 이미 가진 오프셋을 뺀 나머지.
  third_dial_by_jp.json  제3차의 '일본어 원문 -> 한국어 세그먼트' 색인.
                       같은 대사가 EX 에도 나오면 제3차 번역을 그대로 재사용해
                       표기를 통일한다. 제3차 원장 + 제3차 오버레이로 만든다.

    python ex-ui/build_gap_inputs.py
"""
# --- 이식용 부트스트랩 (자동 삽입) ---
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.exists(_os.path.join(_d, "srwcb_paths.py")):
    _d = _os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import srwcb_paths as _P
_P.ensure_dirs()
for _sub in ("tools", "third-ui", "ex-ui", "tr-ui"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------

import json
import re

OUT = _P.REPO / "ex-ui" / "data"
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}
JPRE = re.compile(r"[぀-ヿ一-鿿]")


def _idx2ch():
    doc = json.loads(_P.FONT_MAPPING.read_text(encoding="utf-8"))
    return {r["glyph_index"]: (r.get("character") or "") for r in doc["rows"]}


def _rec_end(buf, s):
    p = s
    while p < len(buf):
        x = buf[p]
        if x == 0xFF:
            return p + 1
        p += 1 if x < 0xEB else (2 if x <= 0xF5 else 1 + CTRL.get(x, 0))
    return s


def _decode(buf, s, e, table):
    out = []
    p = s
    while p < e - 1:
        x = buf[p]
        if x < 0xEB:
            out.append(table.get(x, "")); p += 1
        elif x <= 0xF5:
            out.append(table.get(((x - 0xEB) << 8) | buf[p + 1], "")); p += 2
        elif x == 0xF6:
            out.append("<f6>"); p += 1
        else:
            p += 1 + CTRL.get(x, 0)
    return "".join(out)


def build_sce_gap_real() -> int:
    """E_SCE 의 텍스트 레코드 중 원장이 안 가진 것 = 보충 대상."""
    from analyze_sce_relocation import parse_scenarios
    sce = (_P.EXTRACTED / "EX" / "E_SCE.BIN").read_bytes()
    ledger = json.loads((_P.LEDGER / "ex_translation_ledger.json").read_text(encoding="utf-8"))
    known = {row["source"]["offset"] for row in ledger["occurrences"]
             if row.get("kind") == "scenario"}
    table = _idx2ch()
    out = []
    for s in parse_scenarios(sce):
        for rec in s.records:
            if rec.start in known:
                continue
            jp = _decode(sce, rec.start, rec.end, table)
            body = jp.replace("<f6>", "")
            if len(body) < 2:
                continue
            n = len(JPRE.findall(body))
            if n < 1 or n / len(body) < 0.34:
                continue
            out.append({"off": rec.start, "jp": jp})
    (OUT / "sce_gap_real.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(out)


def build_third_dial_by_jp() -> int:
    """제3차 '일본어 원문 -> 한국어 세그먼트' 색인.

    세그먼트는 <f6> 를 경계로 나눈 리스트다(ex_gap_apply 가 그 형태로 쓴다).
    """
    ledger = json.loads((_P.LEDGER / "third_translation_ledger.json").read_text(encoding="utf-8"))
    overlay = json.loads((_P.TRANSLATION / "third_translation_overlay.json").read_text(encoding="utf-8"))
    tr = overlay["translations"]
    out = {}
    for row in ledger["occurrences"]:
        if row.get("kind") != "scenario":
            continue
        key = row.get("translation_memory_key")
        t = tr.get(key)
        if not t:
            continue
        parts = row["japanese"]["translation_parts"]
        ko_parts = t.get("ko_parts", {})
        jp_txt, segs = [], []
        for part in parts:
            if part["kind"] == "text":
                v = ko_parts.get(part["part_id"])
                if v is None:
                    segs = None
                    break
                segs.append(v)
                jp_txt.append(part.get("ja", ""))
            elif part["kind"] in ("control", "page_break"):
                raw = part.get("raw_hex", "").replace(" ", "").upper()
                if raw.startswith("F6"):
                    segs.append("<f6>")
                    jp_txt.append("<f6>")
        if not segs:
            continue
        jp = "".join(jp_txt)
        if jp:
            out.setdefault(jp, segs)
    (OUT / "third_dial_by_jp.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(out)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    n1 = build_sce_gap_real()
    n2 = build_third_dial_by_jp()
    print(f"sce_gap_real.json      {n1:,} 레코드")
    print(f"third_dial_by_jp.json  {n2:,} 항목")
