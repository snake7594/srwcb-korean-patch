"""Patch the SRW EX standalone image with the ported Korean SLPS_025.29 + relocated data.

 * SLPS_025.29 (same size) -> overwrite in place.
 * E_SCE / BMESS4 / E_DEAD grew -> place in the trailing NULL.DA free region and
   retarget their ISO directory entries.
 * Single MODE2/2352 data-track .cue (proven on the SRW2/SRW3 standalones).
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
import struct, math, shutil, hashlib, sys, os

sys.path.insert(0, str(_P.TOOLS))
from patch_raw_track_exes import SECTOR_SIZE, USER_DATA_OFFSET as UDO, USER_DATA_SIZE as UDS, rebuild_mode2_form1

SRWEX = str(_P.WORK / "srwex")
EXB = str(_P.BUILD / "ex_full")
SRC_IMG = f"{SRWEX}/Super Robot Taisen EX (J).img"
OUT_DIR = f"{SRWEX}/port"; os.makedirs(OUT_DIR, exist_ok=True)
OUT_IMG = f"{OUT_DIR}/Super Robot Taisen EX (Korean).img"
OUT_CUE = f"{OUT_DIR}/Super Robot Taisen EX (Korean).cue"
PATCHED_EXE = f"{SRWEX}/extracted/SLPS_025.29.patched"
RETAIL_EXE = f"{SRWEX}/extracted/SLPS_025.29"

INPLACE = [("SLPS_025.29;1", RETAIL_EXE, PATCHED_EXE)]
RELOC = [("E_SCE.BIN;1", 777512, str(_P.final("EX/E_SCE.BIN", f"{EXB}/rebuilt/EX/E_SCE.BIN"))),
         ("BMESS4.BIN;1", 657036, str(_P.final("BMESS4.BIN", f"{EXB}/rebuilt/BMESS4.BIN"))),
         ("E_DEAD.BIN;1", 4828, str(_P.final("EX/E_DEAD.BIN", f"{EXB}/rebuilt/EX/E_DEAD.BIN")))]
FREE_START, FREE_END = 232784, 246284      # NULL.DA 영역 (이미지 끝 246284 섹터)

def bcd(v): return ((v // 10) << 4) | (v % 10)
def sector_header(lba):
    ab = lba + 150; m, r = divmod(ab, 75 * 60); s, f = divmod(r, 75)
    return bytes((bcd(m), bcd(s), bcd(f), 2))
def make_sector(lba, payload, final):
    sec = bytearray(SECTOR_SIZE)
    sec[0:12] = b"\x00" + b"\xFF" * 10 + b"\x00"
    sec[12:16] = sector_header(lba)
    sub = 0x89 if final else 0x08
    sec[16:20] = bytes((0, 0, sub, 0)); sec[20:24] = sec[16:20]
    sec[UDO:UDO + len(payload)] = payload
    rebuild_mode2_form1(sec); return bytes(sec)
def write_file_at(track, start_lba, payload):
    cnt = math.ceil(len(payload) / UDS)
    for i in range(cnt):
        track.seek((start_lba + i) * SECTOR_SIZE)
        track.write(make_sector(start_lba + i, payload[i * UDS:(i + 1) * UDS], i == cnt - 1))
    return cnt
def find_dir_entry(track, iso_name, limit):
    name = iso_name.encode("ascii"); track.seek(0); data = track.read(limit)
    at = data.find(name)
    while at >= 0:
        s = at - 33
        if s >= 0 and data[s] >= 33 + len(name) and data[s + 32] == len(name) \
           and data[s + 33:s + 33 + len(name)] == name:
            return s
        at = data.find(name, at + 1)
    raise ValueError("dir entry not found: " + iso_name)
def retarget(track, iso_name, new_lba, new_size, exp_old_size, limit):
    ent = find_dir_entry(track, iso_name, limit); slba, off = divmod(ent, SECTOR_SIZE)
    track.seek(slba * SECTOR_SIZE); sec = bytearray(track.read(SECTOR_SIZE))
    old_lba = struct.unpack_from("<I", sec, off + 2)[0]
    old_size = struct.unpack_from("<I", sec, off + 10)[0]
    assert old_size == exp_old_size, f"{iso_name}: dir size {old_size} != {exp_old_size}"
    struct.pack_into("<I", sec, off + 2, new_lba); struct.pack_into(">I", sec, off + 6, new_lba)
    struct.pack_into("<I", sec, off + 10, new_size); struct.pack_into(">I", sec, off + 14, new_size)
    rebuild_mode2_form1(sec); track.seek(slba * SECTOR_SIZE); track.write(sec)
    return old_lba, old_size

shutil.copyfile(SRC_IMG, OUT_IMG)
manifest = []
with open(OUT_IMG, "r+b") as track:
    limit = FREE_START * SECTOR_SIZE
    for iso, srcp, patp in INPLACE:
        src = open(srcp, "rb").read(); pat = open(patp, "rb").read()
        assert len(src) == len(pat), f"{iso} size {len(src)}->{len(pat)}"
        ent = find_dir_entry(track, iso, limit); slba, off = divmod(ent, SECTOR_SIZE)
        track.seek(slba * SECTOR_SIZE); s = track.read(SECTOR_SIZE)
        old_lba = struct.unpack_from("<I", s, off + 2)[0]
        cnt = write_file_at(track, old_lba, pat)
        manifest.append(dict(iso=iso, mode="in-place", lba=old_lba, size=len(pat), sectors=cnt))
    nxt = FREE_START
    for iso, old_size, path in RELOC:
        pay = open(path, "rb").read(); cnt = math.ceil(len(pay) / UDS)
        assert nxt + cnt <= FREE_END, f"{iso}: free region overflow"
        write_file_at(track, nxt, pay)
        old = retarget(track, iso, nxt, len(pay), old_size, limit)
        manifest.append(dict(iso=iso, mode="reloc", old_lba=old[0], new_lba=nxt, size=len(pay), sectors=cnt))
        nxt += cnt

open(OUT_CUE, "w").write(f'FILE "{os.path.basename(OUT_IMG)}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
print("OUT:", OUT_IMG)
for m in manifest: print("  ", m)
print("free used:", nxt - FREE_START, "of", FREE_END - FREE_START, "sectors")
print("cue:", OUT_CUE)
