"""Master build: 제3차 full Korean (dialogue + menus) THIRD.WAR + data archives.

Order matters:
  1. compute FINAL extras from ALL Korean (dialogue + every UI asset)
  2. build_dynamic_font(FINAL extras) -> font into every executable + glyph_map
  3. rebuild 3_SCE / BMESS3 / 3_DEAD with that glyph_map
  4. embedded BMESS3 table + battle-scratch patches onto the font-patched THIRD.WAR
  5. UI injection last (it stores records in unused font glyph slots)
"""
import json, struct, sys, re, shutil, hashlib
from pathlib import Path
ROOT = Path("D:/ps1/roms/SRWCB/korean_patch")
SP = "C:/Users/Jay/AppData/Local/Temp/claude/D--ps1-roms-SRWCB/57133a9b-927c-4883-b4d5-bbcc7cdad986/scratchpad"
sys.path.insert(0, str(ROOT / "tools"))

import rebuild_second_sce as R
R.EXPECTED_POINTER_COUNT = 142; R.EXPECTED_SCENARIO_COUNT = 71
from rebuild_second_sce import rebuild_second_sce
from analyze_second_message_archives import rebuild_bmess_repack, rebuild_dead
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
for x in json.load(open(ROOT/"translation_v2/second_ui_map_labels_overlay.json", encoding="utf-8"))["records"]:
    if x.get("korean_text"): ko.append(x["korean_text"])
for tb in json.load(open(ROOT/"translation_v2/second_ui_tables_overlay.json", encoding="utf-8"))["tables"]:
    ko += [e["korean_text"] for e in tb["entries"] if e.get("korean_text")]
for tb in json.load(open(ROOT/"translation_v2/second_ui_names_overlay.json", encoding="utf-8"))["tables"]:
    ko += [r["korean"] for r in tb["rows"] if r.get("korean")]
for a in json.load(open(ROOT/"translation_v2/second_ui_scripts_overlay.json", encoding="utf-8"))["assets"].values():
    for r in a["records"]:
        ko += [rep["korean_text"] for rep in r.get("replacements", []) if rep.get("korean_text")]
ko += [v for k, v in json.load(open(f"{SP}/third_ui_translations.json", encoding="utf-8")).items() if not k.startswith("_") and v]
dlg_doc = json.load(open(ROOT/"translation_v2/third_translation_overlay.json", encoding="utf-8"))["translations"]
ko += [v for t in dlg_doc.values() for v in t["ko_parts"].values()]
ko = [STRIP.sub("", x) for x in ko]
base = load_safe_glyph_map()
EXTRAS = required_extra_characters([normalise_for_font(x)[0] for x in ko], base)
print("FINAL extras:", EXTRAS)

# ---------- 2) font ----------
glyph_map, font_manifest, dyn = build_dynamic_font(EXTRAS, OUT)
print("font ->", dyn, "extra_glyph_count", font_manifest["extra_glyph_count"])

# ---------- 3) dialogue archives ----------
rows, tr, _ = validate_translation_inputs(ROOT/"research/translation_v2/third_translation_ledger.json",
                                          ROOT/"translation_v2/third_translation_overlay.json")
src_sce = (ROOT/"extracted/THIRD/3_SCE.BIN").read_bytes()
src_bm  = (ROOT/"extracted/BMESS3.BIN").read_bytes()
src_dd  = (ROOT/"extracted/THIRD/3_DEAD.BIN").read_bytes()
sce_r, bm_r, dd_r, _m = make_replacements(rows, tr, glyph_map, src_sce, src_bm, src_dd)
out_sce, _ = rebuild_second_sce(src_sce, sce_r, strict_source=False)
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
