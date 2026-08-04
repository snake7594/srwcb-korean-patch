# -*- coding: utf-8 -*-
"""Build a CB (Complete Box) test/release image from the current runtime THIRD.WAR
+ rebuilt 3_SCE, applying boot/THIRD dialogue-repoint from third_repoint.json.

Usage:  python build_cb_image.py <output_name.bin>
(the intermediate CB_v094test3.bin is deleted; repoint now comes from JSON)
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
# ------------------------------------------------------------------
import struct, math, shutil, hashlib, sys, os, json

sys.path.insert(0, str(_P.TOOLS))
from patch_raw_track_exes import patch_one_executable, rebuild_mode2_form1, SECTOR_SIZE

ROOT = str(_P.WORK)
FULL = f"{ROOT}/test_build/third_full"
SP = str(_P.BUILD)
BASE = f"{FULL}/Super Robot Taisen Complete Box Second+Third Korean v0.9.3 (Track 1).bin"
UDO, UDS = 24, 2048
BOOT_LBA, THIRD_LBA = 0x3a6f2, 0x68cb
BOOTSZ, THIRDSZ = 763904, 1228800
SCE_LBA, DIR_LBA, DIR_OFF = 0x3b14e, 0x6778, 0x9c

def read_lba(img, lba, n):
    out = bytearray()
    with open(img, "rb") as f:
        for i in range(math.ceil(n / UDS)):
            f.seek((lba + i) * SECTOR_SIZE); s = f.read(SECTOR_SIZE)
            out.extend(s[UDO:UDO + UDS])
    return bytes(out[:n])

def main(outname):
    rp = json.load(open(f"{SP}/third_repoint.json"))
    boot_diffs = {int(k, 16): v for k, v in rp["boot_diffs"].items()}
    third_rep = {int(k, 16): v for k, v in rp["third_rep"].items()}
    new_sce = open(f"{FULL}/rebuilt/THIRD/3_SCE.BIN", "rb").read()
    assert math.ceil(len(new_sce) / 2048) <= 377, "3_SCE exceeds reserved 377 sectors"
    third093 = read_lba(BASE, THIRD_LBA, THIRDSZ)
    war = bytearray(open(f"{FULL}/runtime/THIRD/THIRD.WAR", "rb").read())
    for i, b in third_rep.items():
        assert war[i] == third093[i], f"repoint src mismatch @{i:#x}"
        war[i] = b
    out = f"{FULL}/{outname}"
    if os.path.exists(out): os.remove(out)
    shutil.copyfile(BASE, out)
    with open(out, "r+b") as track:
        sb = read_lba(out, BOOT_LBA, BOOTSZ); pb = bytearray(sb)
        for i, b in boot_diffs.items(): pb[i] = b
        patch_one_executable(track, BOOT_LBA, sb, bytes(pb), "SLPS_020.70")
        st = read_lba(out, THIRD_LBA, THIRDSZ)
        patch_one_executable(track, THIRD_LBA, st, bytes(war), "THIRD.WAR")
        nsec = 377; payload = new_sce + b"\x00" * (nsec * 2048 - len(new_sce))
        for si in range(nsec):
            track.seek((SCE_LBA + si) * SECTOR_SIZE)
            sec = bytearray(track.read(SECTOR_SIZE))
            sec[UDO:UDO + UDS] = payload[si * 2048:(si + 1) * 2048]
            rebuild_mode2_form1(sec)
            track.seek((SCE_LBA + si) * SECTOR_SIZE); track.write(sec)
        track.seek(DIR_LBA * SECTOR_SIZE); raw = bytearray(track.read(SECTOR_SIZE))
        ud = raw[UDO:UDO + UDS]
        assert ud[DIR_OFF + 33:DIR_OFF + 42] == b"3_SCE.BIN"
        struct.pack_into("<I", ud, DIR_OFF + 10, len(new_sce))
        struct.pack_into(">I", ud, DIR_OFF + 14, len(new_sce))
        raw[UDO:UDO + UDS] = ud; rebuild_mode2_form1(raw)
        track.seek(DIR_LBA * SECTOR_SIZE); track.write(raw)
    stem = outname[:-4] if outname.endswith(".bin") else outname
    cue = (f'FILE "{outname}" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'
           f'FILE "Super Robot Taisen Complete Box (Track 2).bin" BINARY\r\n'
           f'  TRACK 02 AUDIO\r\n    INDEX 00 00:00:00\r\n    INDEX 01 00:02:00\r\n')
    open(f"{FULL}/{stem}.cue", "wb").write(cue.encode())
    # verify
    w = read_lba(out, THIRD_LBA, THIRDSZ); boot2 = read_lba(out, BOOT_LBA, BOOTSZ)
    sce2 = read_lba(out, SCE_LBA, len(new_sce))
    MH = 0x247CC
    tbl = [MH + 4 + 4 * i + struct.unpack_from("<i", w, MH + 4 + 4 * i)[0] for i in range(107)]
    CAVES = [[0x113f1d, 0x114f88], [0x1134ec, 0x113910], [0x10d2c5, 0x10d548],
             [0x119854, 0x119a56], [0x1196c8, 0x119852], [0x11af23, 0x11b028]]
    def cu(buf, v):
        tb = struct.pack("<I", v); i = c = 0
        while True:
            j = buf.find(tb, i)
            if j < 0: break
            c += 1; i = j + 1
        return c
    ok = (hashlib.sha256(sce2).hexdigest() == hashlib.sha256(new_sce).hexdigest()
          and tbl[11] == 0x25efc and tbl[12] == 0x25f67
          and all(all(x == 0 for x in w[a:b]) for a, b in CAVES)
          and cu(boot2, 0x3b2c7) == 11 and cu(boot2, 0x6779) == 0)
    print(f"OUT {out} ({os.path.getsize(out)} B)")
    print(f"VERIFY {'PASS' if ok else 'FAIL'}: sce/anchors/caves/repoint")
    assert ok

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "CB_v094test.bin")
