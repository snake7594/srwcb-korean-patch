"""Master build: 제3차 full Korean (dialogue + menus) THIRD.WAR + data archives.

Order matters:
  1. compute FINAL extras from ALL Korean (dialogue + every UI asset)
  2. build_dynamic_font(FINAL extras) -> font into every executable + glyph_map
  3. rebuild 3_SCE / BMESS3 / 3_DEAD with that glyph_map
  4. embedded BMESS3 table + battle-scratch patches onto the font-patched THIRD.WAR
  5. UI injection last (it stores records in unused font glyph slots)
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
for _sub in ("tools", "third-ui", "ex-ui", "tr-ui", "audit", "menu-align", "second-fixes"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import json, os, struct, sys, re, shutil, hashlib
from pathlib import Path
ROOT = _P.WORK
SP = str(_P.BUILD)
sys.path.insert(0, str(_P.TOOLS))

import rebuild_second_sce as R
R.EXPECTED_POINTER_COUNT = 142; R.EXPECTED_SCENARIO_COUNT = 71
from rebuild_second_sce import rebuild_second_sce
from analyze_second_message_archives import rebuild_bmess_repack, rebuild_dead
# 작전목적(승리/패배조건) 블록을 레트일 배치로 못박는다 — **제3차만** 필요하다.
# 제3차 작전목적 화면은 이 레코드들이 원문 자리에 없으면 엉뚱한 대사를 물어 온다
# (2026-08-09 제보: 승리조건 빈칸, 패배조건에 대사). 제2차·EX 는 해당 없음.
os.environ["SRWCB_PIN_OBJECTIVE"] = "1"
from build_second_expanded_patch import (validate_translation_inputs, make_replacements,
                                          build_dynamic_font, replace_unique_equal_sized_blob,
                                          parse_message_record)
from analyze_second_message_archives import analyze_bmess_runtime_scratch
from second_translation_codec import load_safe_glyph_map, required_extra_characters, normalise_for_font

STRIP = re.compile(r"\[F[6-9A-Ea-e]\]")
OUT = ROOT / "test_build/third_full"
OUT.mkdir(parents=True, exist_ok=True)

# ---------- 1) FINAL extras ----------
ko = []
for x in json.load(open((_P.TRANSLATION / "second_ui_map_labels_overlay.json"), encoding="utf-8"))["records"]:
    if x.get("korean_text"): ko.append(x["korean_text"])
for tb in json.load(open((_P.TRANSLATION / "second_ui_tables_overlay.json"), encoding="utf-8"))["tables"]:
    ko += [e["korean_text"] for e in tb["entries"] if e.get("korean_text")]
for tb in json.load(open((_P.TRANSLATION / "second_ui_names_overlay.json"), encoding="utf-8"))["tables"]:
    ko += [r["korean"] for r in tb["rows"] if r.get("korean")]
for a in json.load(open((_P.TRANSLATION / "second_ui_scripts_overlay.json"), encoding="utf-8"))["assets"].values():
    for r in a["records"]:
        ko += [rep["korean_text"] for rep in r.get("replacements", []) if rep.get("korean_text")]
ko += [v for k, v in json.load(open(f"{_P.TRANSLATION}/third_ui_translations.json", encoding="utf-8")).items() if not k.startswith("_") and v]
dlg_doc = json.load(open((_P.TRANSLATION / "third_translation_overlay.json"), encoding="utf-8"))["translations"]
ko += [v for t in dlg_doc.values() for v in t["ko_parts"].values()]
# supplemental gap translations (ledger-missed records) + system message pool
sys.path.insert(0, SP)
from sce_gap_translations import DIAL as _GAP_DIAL, PHRASES as _GAP_PHRASES
ko += [s for segs in _GAP_DIAL.values() for s in segs if not (s.startswith("<") and s.endswith(">"))]
ko += [k2 for _, k2 in _GAP_PHRASES]
import os as _os
if _os.path.exists(f"{_P.TRANSLATION}/msgpool_translations.json"):
    ko += [v for v in json.load(open(f"{_P.TRANSLATION}/msgpool_translations.json", encoding="utf-8")).values() if v]
from third_align_overrides import ALIGN_KO_TEXTS as _AKT
ko += _AKT
ko = [STRIP.sub("", x) for x in ko]
base = load_safe_glyph_map()
EXTRAS = required_extra_characters([normalise_for_font(x)[0] for x in ko], base)
print("FINAL extras:", EXTRAS)

# ---------- 2) font ----------
glyph_map, font_manifest, dyn = build_dynamic_font(EXTRAS, OUT)
print("font ->", dyn, "extra_glyph_count", font_manifest["extra_glyph_count"])

# ---------- 3) dialogue archives ----------
rows, tr, _ = validate_translation_inputs((_P.LEDGER / "third_translation_ledger.json"),
                                          (_P.TRANSLATION / "third_translation_overlay.json"))
src_sce = ((_P.EXTRACTED / "THIRD/3_SCE.BIN")).read_bytes()
src_bm  = ((_P.EXTRACTED / "BMESS3.BIN")).read_bytes()
src_dd  = ((_P.EXTRACTED / "THIRD/3_DEAD.BIN")).read_bytes()
sce_r, bm_r, dd_r, _m = make_replacements(rows, tr, glyph_map, src_sce, src_bm, src_dd)
# merge supplemental replacements for the 159 ledger-missed records
from sce_gap_supplement import build_supplement
_idx2ch = {r["glyph_index"]: (r.get("character") or "")
           for r in json.load(open(ROOT/"research/srwcb_embedded_font_mapping_reviewed.json", encoding="utf-8"))["rows"]}
_sup, _rep = build_supplement(glyph_map, src_sce, _idx2ch, SP)
for _k, _v in _sup.items():
    assert _k not in sce_r, f"supplement collides with ledger record {_k:#x}"
    sce_r[_k] = _v
print(f"supplemental gap records: {len(_sup)}  {_rep}")
# global leftover pass: condition phrases in records the gap scan missed
# (kanji-heavy records fall under the kana>=3 heuristic) or inside ledger
# replacements -- substitute wherever the JP patterns remain
from sce_gap_supplement import make_encoder, make_pats, apply_phrases
_enc = make_encoder(glyph_map, _idx2ch)
_pats = make_pats(_enc)
_left = 0
for _sc in R.parse_scenarios(src_sce):
    for _r in _sc.records:
        _base = sce_r.get(_r.start, bytes(src_sce[_r.start:_r.end]))
        _new = apply_phrases(_base, _pats, _idx2ch)
        if _new is not None:
            sce_r[_r.start] = _new; _left += 1
print(f"leftover condition records phrase-subbed: {_left}")
import fix_sce_event_refs as _FX
# 진단 전용(기본 꺼짐): 레코드를 원문과 똑같은 바이트 길이로 맞춘다.
#
# 한동안 이게 제3차 대사 꼬임의 해법이었지만, 진짜 원인은 따로 있었다:
# 대사 포인터 명령에 `B6 00 <변위16>` 형태가 있는데(피연산자가 옵코드+2)
# 재조준기가 `B1/B3/B4 <변위16>`(+1)만 알고 있어서 221곳을 놓쳤다. 그 대사들만
# 원문 자리에 남아, 레코드를 옮긴 만큼 밀려 나왔다. → 2026-08-09 수정.
# 자세한 근거는 tools/analyze_sce_relocation.iter_pointer_sites 주석 참조.
#
# 이 경로를 켜면 번역 꼬리가 잘린다(제3차 3,475개 34,699B). 비교용으로만 남긴다.
if os.environ.get("SRWCB_EXACT_LEN", "") not in ("", "0"):
    from second_translation_codec import fit_exact_length
    from analyze_sce_relocation import parse_scenarios as _PS
    _want = {}
    for _s in _PS(bytes(src_sce)):
        for _r in _s.records:
            _want[_r.start] = _r.end - _r.start
    from build_second_expanded_patch import _EXACT_CUTS
    if _EXACT_CUTS:
        import json as _j
        _j.dump([{"id": i, "off": o, "budget": w, "over": n, "ko": k}
                 for i, o, w, n, k in sorted(_EXACT_CUTS, key=lambda z: -z[3])],
                open(f"{SP}/third_ledger_cuts.json", "w", encoding="utf-8"),
                ensure_ascii=False, indent=1)
        print(f"   원장 대사 절단: {len(_EXACT_CUTS)}개 "
              f"(총 {sum(z[3] for z in _EXACT_CUTS)}B) -> third_ledger_cuts.json")
    _pad = _cut = 0; _lost = 0; _bad = []; _pre = {}
    for _off in list(sce_r):
        _w = _want.get(_off)
        if not _w or len(sce_r[_off]) == _w:
            continue
        if len(sce_r[_off]) > _w:
            _cut += 1; _lost += len(sce_r[_off]) - _w
            _bad.append((_off, len(sce_r[_off]) - _w)); _pre[_off] = sce_r[_off]
        else:
            _pad += 1
        sce_r[_off] = fit_exact_length(sce_r[_off], _w)
    print(f"정확 길이 보정: 공백채움 {_pad} / 잘림 {_cut} (총 {_lost}B)")
    if _bad:
        _bad.sort(key=lambda z: -z[1])
        print("   가장 많이 넘는 것:", [(hex(o), n) for o, n in _bad[:10]])
        import json as _json
        def _dec(_b):
            _s = ""; _i = 0
            while _i < len(_b):
                _x = _b[_i]
                if _x == 0xFF: break
                if _x < 0xEB: _s += _idx2ch.get(_x, "?"); _i += 1
                elif _x < 0xF6:
                    _s += _idx2ch.get(((_x - 0xEB) << 8) | _b[_i + 1], "?"); _i += 2
                else:
                    _s += {0xF6: "⏎", 0xF7: "▶"}.get(_x, f"<{_x:02x}>")
                    _i += 1 + {0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}.get(_x, 0)
            return _s
        _json.dump([{"off": o, "over": n, "budget": _want[o],
                     "ko": _dec(_pre[o]), "jp": _dec(bytes(src_sce[o:o + _want[o]]))}
                    for o, n in _bad],
                   open(f"{SP}/third_overbudget.json", "w", encoding="utf-8"), indent=1)
        print(f"   [주의] 원문 예산을 넘어 잘린 대사 {_cut}개 (총 {_lost}B) — "
              f"번역을 줄여야 합니다: {SP}/third_overbudget.json")
        if os.environ.get("SRWCB_STRICT_LEN"):
            raise SystemExit("SRWCB_STRICT_LEN: 잘림이 남아 빌드를 멈춥니다")

from second_translation_codec import pin_objective_block as _PIN
_PIN(src_sce, sce_r, label=" 제3차")
sce_r, out_sce = _FX.harden_against_ff_operands(
    src_sce, sce_r,
    lambda s, r: rebuild_second_sce(s, r, strict_source=False))
out_bm = rebuild_bmess_repack(src_bm, bm_r)
out_dd = rebuild_dead(src_dd, dd_r)
reb = OUT/"rebuilt"; (reb/"THIRD").mkdir(parents=True, exist_ok=True)
(reb/"THIRD/3_SCE.BIN").write_bytes(out_sce); (reb/"BMESS3.BIN").write_bytes(out_bm); (reb/"THIRD/3_DEAD.BIN").write_bytes(out_dd)
print(f"dialogue: 3_SCE {len(out_sce)}  BMESS3 {len(out_bm)}  3_DEAD {len(out_dd)}")

# ---------- 4) runtime patches on the font-patched THIRD.WAR ----------
war = bytearray((dyn/"THIRD/THIRD.WAR").read_bytes())
def u32(b,o): return struct.unpack_from("<I",b,o)[0]
assert war[:8]==b"PS-X EXE" and len(war)==0x12c000
tsz=u32(war,0x1C); assert len(war)==tsz+0x800 and u32(war,0x18)+tsz==0x8013b800
# embedded BMESS3 outer table
tblsz=u32(src_bm,0); assert u32(out_bm,0)==tblsz
patched,off = replace_unique_equal_sized_blob(bytes(war), src_bm[:tblsz], out_bm[:tblsz])
war=bytearray(patched); print(f"embedded BMESS3 table @0x{off:x} ({tblsz} B)")
# battle scratch
lens=[]
for i in range(400):
    f=0x10DBFC+i*4; t=f+struct.unpack_from("<i",war,f)[0]
    rec=parse_message_record(bytes(war),t); lens.append(rec.end-rec.start-1)
a=analyze_bmess_runtime_scratch(out_bm, tuple(lens))
assert a["maximum_bytes"]<=0x200 and a["maximum_leaf_count"]*10+2<=0x80, a["maximum_bytes"]
def patch(o,src,pat):
    s=bytes.fromhex(src.replace(" ","")); p=bytes.fromhex(pat.replace(" ",""))
    assert war[o:o+len(s)]==s, f"scratch src mismatch @{o:#x}"
    war[o:o+len(p)]=p
assert u32(war,0x800)==0x8015CC08
struct.pack_into("<I",war,0x800,0x8015D408)
patch(0x44A04,"16 80 03 3C 08 CC 63 24","16 80 03 3C 08 D4 63 24")
patch(0x44A4C,"16 80 04 3C 08 CC 84 24","16 80 04 3C 08 D4 84 24")
assert war[0x44A8C:0x44A90]==bytes.fromhex("04008420")
patch(0xC3890,"18 80 03 3C 1C 2C 63 34 00 12 04 00","15 80 03 3C 08 CC 63 34 40 12 04 00")
print(f"battle scratch OK (need {a['maximum_bytes']:#x}/0x200)")
runtime = OUT/"runtime"; (runtime/"THIRD").mkdir(parents=True, exist_ok=True)
(runtime/"THIRD/THIRD.WAR").write_bytes(bytes(war))
print("runtime THIRD.WAR sha", hashlib.sha256(bytes(war)).hexdigest()[:16])
json.dump({"extras":EXTRAS}, open(f"{SP}/third_final_extras.json","w",encoding="utf-8"), ensure_ascii=False)
print("\nNEXT: run inject_third_ui3.py with SRC =", runtime/"THIRD/THIRD.WAR")
