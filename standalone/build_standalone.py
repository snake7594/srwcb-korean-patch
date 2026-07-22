"""Build the SRW2 standalone (SLPS_024.06) Korean patch from the Complete Box
build, end to end.  Run after the Complete Box build has produced the patched
SECOND.WAR and rebuilt data files (config.CB_BUILD).

Steps:
  1. extract retail files from the SRW2 image + derive music pointer fields
  2. build the SECOND.WAR -> SLPS_024.06 delta map
  3. transplant the executable patch onto SLPS_024.06 (port_exe)
  4. content-validate the ported executable
  5. patch the SRW2 image (in-place SLPS, relocate grown data files) + write .cue

Emits the playable image at config.OUT_IMG and a single-data-track config.OUT_CUE.
"""
import runpy, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import config

def run(step, mod):
    print(f"\n===== {step} =====")
    runpy.run_path(str(HERE / mod), run_name="__main__")

run("1/5 extract SRW2 retail files + music fields", "extract_srw2.py")
run("2/5 build delta map", "build_delta_map.py")
run("3/5 transplant executable patch", "port_exe.py")
run("4/5 validate ported executable", "validate_content.py")
run("5/5 patch image + write cue", "patch_iso.py")

print("\n=== DONE ===")
print("image:", config.OUT_IMG)
print("cue  :", config.OUT_CUE, "(open THIS in DuckStation; .ccd freezes)")
