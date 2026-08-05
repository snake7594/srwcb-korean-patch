"""Master build: EX full Korean (dialogue + tables) EX.WAR + data archives.

Mirrors build_third_full.py but for EX, with two EX-specific guards:

  * extras order is PINNED (existing 10 keep their glyph indices) so the
    already-shipped SECOND/THIRD text can never shift -- see EX_PLAN.md B-1.
  * only EX.WAR's own font blob is rewritten; SECOND.WAR/THIRD.WAR are not
    touched at all, so 제2차/제3차 cannot regress.

Order: extras -> font -> E_SCE/BMESS4/E_DEAD rebuild -> EX.WAR runtime patches.
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
if str(_P.TOOLS) not in _sys.path:
    _sys.path.insert(0, str(_P.TOOLS))
# ------------------------------------------------------------------
import json, struct, sys, re, hashlib
from pathlib import Path

ROOT = _P.WORK
SP = str(_P.BUILD)
sys.path.insert(0, str(_P.TOOLS))
sys.path.insert(0, SP)

import rebuild_second_sce as R
R.EXPECTED_POINTER_COUNT = 144
R.EXPECTED_SCENARIO_COUNT = 72
from rebuild_second_sce import rebuild_second_sce
from analyze_second_message_archives import (rebuild_bmess_repack, rebuild_dead,
                                             analyze_bmess_runtime_scratch)
from build_second_expanded_patch import (validate_translation_inputs, make_replacements,
                                         build_dynamic_font, replace_unique_equal_sized_blob,
                                         parse_message_record)
from second_translation_codec import (load_safe_glyph_map, required_extra_characters,
                                      normalise_for_font)

STRIP = re.compile(r"\[F[6-9A-Ea-e]\]")
OUT = ROOT / "test_build/ex_full"
OUT.mkdir(parents=True, exist_ok=True)

# 기존 배포본이 쓰는 extras 순서 (절대 변경 금지 - 앞 10개 인덱스가 고정돼야 함)
PINNED = ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응']

# ---------- 1) Korean corpus -> PINNED extras ----------
ko = []
ex_overlay = json.load(open(f"{SP}/ex/ex_translation_overlay.json", encoding="utf-8"))["translations"]
ko += [v for t in ex_overlay.values() for v in t["ko_parts"].values() if v]
_uip = f"{SP}/ex/ex_ui_translations.json"
import os
if os.path.exists(_uip):
    ko += [v for k, v in json.load(open(_uip, encoding="utf-8")).items()
           if not k.startswith("_") and v]
ko = [STRIP.sub("", x) for x in ko]
base = load_safe_glyph_map()
need = required_extra_characters([normalise_for_font(x)[0] for x in ko], base)
EXTRAS = PINNED + sorted(c for c in need if c not in PINNED)
assert EXTRAS[:len(PINNED)] == PINNED, "extras 순서가 흔들렸습니다 (기존 게임 깨짐 위험)"
print(f"EXTRAS {len(EXTRAS)}개 (고정 {len(PINNED)} + EX신규 {len(EXTRAS)-len(PINNED)}): {EXTRAS[len(PINNED):]}")

# ---------- 2) font (EX.WAR 사본만 사용) ----------
glyph_map, font_manifest, dyn = build_dynamic_font(EXTRAS, OUT)
print("font ->", dyn, "extra_glyph_count", font_manifest["extra_glyph_count"])

# ---------- 3) dialogue archives ----------
rows, tr, _ = validate_translation_inputs(ROOT / "research/translation_v2/ex_translation_ledger.json",
                                          Path(f"{SP}/ex/ex_translation_overlay.json"))
src_sce = (ROOT / "extracted/EX/E_SCE.BIN").read_bytes()
src_bm = (ROOT / "extracted/BMESS4.BIN").read_bytes()
src_dd = (ROOT / "extracted/EX/E_DEAD.BIN").read_bytes()
sce_r, bm_r, dd_r, _m = make_replacements(rows, tr, glyph_map, src_sce, src_bm, src_dd)
# 원장이 놓친 레코드 보충(조건문 구문치환 + 순수 텍스트 전체 교체).
# 미번역으로 남으면 한글 폰트 때문에 일본어가 깨진 한글로 렌더된다.
_idx2ch = {r["glyph_index"]: (r.get("character") or "")
           for r in json.load(open(ROOT / "research/srwcb_embedded_font_mapping_reviewed.json",
                                   encoding="utf-8"))["rows"]}
from ex_gap_apply import build_ex_supplement
_sup, _rep = build_ex_supplement(glyph_map, src_sce, _idx2ch)
_rep.pop("over_width_lines", None)
_dup = [k for k in _sup if k in sce_r]
assert not _dup, f"보충이 원장 레코드와 충돌: {[hex(x) for x in _dup[:5]]}"
sce_r.update(_sup)
print(f"E_SCE 보충: {_rep}  (총 {len(_sup)} 레코드)")
out_sce, _ = rebuild_second_sce(src_sce, sce_r, strict_source=False)
out_bm = rebuild_bmess_repack(src_bm, bm_r)
out_dd = rebuild_dead(src_dd, dd_r)
reb = OUT / "rebuilt"; (reb / "EX").mkdir(parents=True, exist_ok=True)
(reb / "EX/E_SCE.BIN").write_bytes(out_sce)
(reb / "BMESS4.BIN").write_bytes(out_bm)
(reb / "EX/E_DEAD.BIN").write_bytes(out_dd)
print(f"dialogue: E_SCE {len(out_sce)}  BMESS4 {len(out_bm)}  E_DEAD {len(out_dd)}")

# ---------- 4) runtime patches on the font-patched EX.WAR ----------
war = bytearray((dyn / "EX/EX.WAR").read_bytes())
def u32(b, o): return struct.unpack_from("<I", b, o)[0]
assert war[:8] == b"PS-X EXE" and len(war) == 0x124000, "EX.WAR 크기/매직 이상"
# embedded BMESS4 outer table (EX.WAR 0x10431c, unique)
tblsz = u32(src_bm, 0); assert u32(out_bm, 0) == tblsz
patched, off = replace_unique_equal_sized_blob(bytes(war), src_bm[:tblsz], out_bm[:tblsz])
war = bytearray(patched); print(f"embedded BMESS4 table @0x{off:x} ({tblsz} B)")
# battle scratch 용량 확인 (EX pilot_short_names 필드 = 0x107790)
lens = []
for i in range(400):
    f = 0x107790 + i * 4; t = f + struct.unpack_from("<i", war, f)[0]
    rec = parse_message_record(bytes(war), t); lens.append(rec.end - rec.start - 1)
a = analyze_bmess_runtime_scratch(out_bm, tuple(lens))
print(f"battle scratch 필요 {a['maximum_bytes']:#x} (leaf {a['maximum_leaf_count']})")
json.dump({"scratch": a}, open(f"{SP}/ex/scratch_report.json", "w"), indent=1, default=str)

runtime = OUT / "runtime"; (runtime / "EX").mkdir(parents=True, exist_ok=True)
(runtime / "EX/EX.WAR").write_bytes(bytes(war))
print("runtime EX.WAR sha", hashlib.sha256(bytes(war)).hexdigest()[:16])
json.dump({"extras": EXTRAS}, open(f"{SP}/ex/ex_final_extras.json", "w", encoding="utf-8"), ensure_ascii=False)
print("\nNEXT: inject_ex_ui.py with SRC =", runtime / "EX/EX.WAR")
