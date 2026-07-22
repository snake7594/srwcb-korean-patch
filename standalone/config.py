"""Shared paths for the SRW2 standalone (SLPS_024.06) Korean-patch port.

The standalone patch is *derived* from the Complete Box build: it transplants
the proven v0.8.7 SECOND.WAR executable patch onto the standalone SLPS_024.06
(different offsets) and reuses the CB rebuilt data files.  Point CB_BUILD at
the latest Complete Box build directory to re-derive the standalone in sync.
"""
from pathlib import Path

# --- Complete Box project (source of the proven patch) ---
CB_ROOT = Path("D:/ps1/roms/SRWCB/korean_patch")
CB_BUILD = CB_ROOT / "test_build" / "second_korean_v0.8.7-full-menus"
CB_RETAIL_SECOND = CB_ROOT / "extracted" / "SECOND" / "SECOND.WAR"
CB_PATCHED_SECOND = CB_BUILD / "font_extracted" / "SECOND" / "SECOND.WAR"
CB_REBUILT = CB_BUILD / "rebuilt"
CB_INVENTORY = CB_ROOT / "research" / "second_exe_ui_full_inventory.json"

# --- SRW2 standalone (user-owned game image; NOT distributed) ---
SRW2_ROOT = Path("D:/ps1/roms/SRW2")
SRW2_IMG = SRW2_ROOT / "Super Robot Taisen 2.img"
SRW2_EXTRACTED = SRW2_ROOT / "extracted"          # retail files pulled from the image
SRW2_RETAIL_EXE = SRW2_EXTRACTED / "SLPS_024.06"

# --- standalone build outputs ---
OUT_DIR = SRW2_ROOT / "port"
DELTA_MAP = OUT_DIR / "delta_map.json"
MUSIC_FIELDS = OUT_DIR / "music_pointer_fields.json"
PATCHED_EXE = OUT_DIR / "SLPS_024.06.patched"
OUT_IMG = OUT_DIR / "Super Robot Taisen 2 (Korean).img"
OUT_CUE = OUT_DIR / "Super Robot Taisen 2 (Korean).cue"

# Data files that grew and must be relocated (retail is byte-identical to CB).
# 2_SCE differs from CB by +8 bytes (one scenario block); the CB patched 2_SCE is
# self-contained and plays that scenario with the CB (same-game, translated) script.
RELOC_FILES = [
    ("BMESS2.BIN;1", "BMESS2.BIN",        "BMESS2.BIN"),
    ("2_DEAD.BIN;1", "SECOND/2_DEAD.BIN", "SECOND/2_DEAD.BIN"),
    ("2_SCE.BIN;1",  "SECOND/2_SCE.BIN",  "SECOND/2_SCE.BIN"),
]
NULL_DA_FREE_START = 232670   # trailing null padding region (LBA)
NULL_DA_FREE_END = 246170

TOOLS = CB_ROOT / "tools"     # for patch_raw_track_exes.rebuild_mode2_form1
