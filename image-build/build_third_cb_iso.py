"""Assemble the combined 제2차(full-menus)+제3차(dialogue) CB track.

Base = v0.8.7 output track (SECOND done). Relocate the 3 grown THIRD archives
(3_SCE/BMESS3/3_DEAD) via ISO directory retarget; overwrite THIRD.WAR in-place
at its fixed LBA 0x68cb with the font+embedded-bmess+battle-scratch build.
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
import sys, struct, hashlib, math
from pathlib import Path
ROOT = _P.WORK
sys.path.insert(0, str(_P.TOOLS))
from relocate_expanded_iso_files import relocate_files
from patch_raw_track_exes import (EXE_LAYOUT, SECTOR_SIZE, USER_DATA_OFFSET, USER_DATA_SIZE,
                                   patch_one_executable)

BASE = ROOT / "test_build/second_korean_v0.8.7-full-menus/Super Robot Taisen Complete Box Second Korean v0.8.7-full-menus (Track 1).bin"
OUT  = ROOT / "test_build/third_korean/Super Robot Taisen Complete Box Second+Third Korean v0.9.0-third-dialogue (Track 1).bin"
ORIG = ROOT / "Super Robot Taisen Complete Box (Track 1).bin"
REB  = ROOT / "test_build/third_korean/rebuilt"
THIRD_WAR_NEW = ROOT / "test_build/third_korean/thirdwar_runtime/THIRD/THIRD.WAR"
EXTRACTED = ROOT / "extracted"

def sha(b): return hashlib.sha256(b).hexdigest()[:16]
def read_lba(track, lba, size):
    out=bytearray()
    n=math.ceil(size/USER_DATA_SIZE)
    for i in range(n):
        track.seek((lba+i)*SECTOR_SIZE); s=track.read(SECTOR_SIZE)
        out.extend(s[USER_DATA_OFFSET:USER_DATA_OFFSET+USER_DATA_SIZE])
    return bytes(out[:size])

# 1) relocate the 3 grown archives (creates OUT as a copy of BASE first)
files=[
    ("BMESS3.BIN;1", EXTRACTED/"BMESS3.BIN",        REB/"BMESS3.BIN"),
    ("3_SCE.BIN;1",  EXTRACTED/"THIRD/3_SCE.BIN",   REB/"THIRD/3_SCE.BIN"),
    ("3_DEAD.BIN;1", EXTRACTED/"THIRD/3_DEAD.BIN",  REB/"THIRD/3_DEAD.BIN"),
]
reloc = relocate_files(BASE, OUT, files)
for f in reloc["files"]:
    print(f"[relocate] {f['iso_name']}: LBA {f['old_lba']}->{f['new_lba']} size {f['old_size']}->{f['new_size']} ({f['sectors']} sec)")

# 2) overwrite THIRD.WAR in-place at its fixed LBA
lba = next(v for k, v in EXE_LAYOUT.items() if str(k).replace("\\", "/").endswith("THIRD.WAR"))
new_war = THIRD_WAR_NEW.read_bytes()
with OUT.open("r+b") as track:
    cur_war = read_lba(track, lba, len(new_war))   # current (v0.8.7 font-only) THIRD.WAR as source
    patch_one_executable(track, lba, cur_war, new_war, "THIRD/THIRD.WAR")
print(f"[exe] THIRD.WAR overwritten in-place @LBA {lba:#x} ({len(new_war)} B) sha={sha(new_war)}")

# 3) verify: read back all 4 from OUT, compare to rebuilt
def find_entry_size_lba(track, name):
    from relocate_expanded_iso_files import find_directory_entry_stream
    e=find_directory_entry_stream(track, name.encode("ascii"))
    track.seek(e+2); f=track.read(16)
    return struct.unpack_from("<I",f,0)[0], struct.unpack_from("<I",f,8)[0]
print("\n[verify] read-back from OUT track:")
ok=True
with OUT.open("rb") as track:
    for iso,exp in [("BMESS3.BIN;1",REB/"BMESS3.BIN"),("3_SCE.BIN;1",REB/"THIRD/3_SCE.BIN"),("3_DEAD.BIN;1",REB/"THIRD/3_DEAD.BIN")]:
        lba_,size_=find_entry_size_lba(track,iso)
        got=read_lba(track,lba_,size_); want=exp.read_bytes()
        m=(sha(got)==sha(want) and size_==len(want)); ok&=m
        print(f"  {iso}: LBA {lba_} size {size_} sha {sha(got)} == rebuilt {sha(want)} : {'OK' if m else 'MISMATCH'}")
    got=read_lba(track,lba,len(new_war)); m=(sha(got)==sha(new_war)); ok&=m
    print(f"  THIRD.WAR: LBA {lba:#x} sha {sha(got)} == {sha(new_war)} : {'OK' if m else 'MISMATCH'}")
print("\nALL VERIFY:", "PASS" if ok else "FAIL")
print("track size:", OUT.stat().st_size, "base:", BASE.stat().st_size, "grew:", OUT.stat().st_size-BASE.stat().st_size)
print("OUT:", OUT)
