# -*- coding: utf-8 -*-
"""v0.9.4 CB 이미지(제2차+제3차 한글) 위에 EX 한글을 얹어 새 이미지를 만든다.

  * EX.WAR  : 크기 동일 -> 제자리 패치 (PS-EXE)
  * E_SCE / BMESS4 / E_DEAD : 커져서 NULL.DA 빈 영역으로 재배치 + ISO 디렉터리 갱신
  * EX는 하드코딩 파일-LBA 테이블이 없음(정찰 확인) -> repoint 불필요, ISO9660으로 로드됨

사용: python build_cb_ex_image.py <출력파일명.bin>
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
from patch_raw_track_exes import (patch_one_executable, rebuild_mode2_form1,
                                  SECTOR_SIZE, USER_DATA_OFFSET as UDO, USER_DATA_SIZE as UDS)

ROOT = str(_P.WORK)
FULL = f"{ROOT}/test_build/third_full"
EXB = f"{ROOT}/test_build/ex_full"
BASE = f"{FULL}/Super Robot Taisen Complete Box Korean v0.9.4 (Track 1).bin"

EX_WAR_LBA, EX_WAR_SZ = 0x63b1, 1196032
# 제3차 재배치분(BMESS3 241661 / 3_SCE 241998 / 3_DEAD 242375+4)이 연속으로 끝나는 지점.
# v0.9.4 이미지는 정확히 242379 섹터에서 끝나므로 EX 파일은 그 뒤에 이어 붙여 이미지를 확장한다
# (제3차 빌드도 레트일 240452 섹터에서 같은 방식으로 확장했고 실기 검증됨).
FREE_START, FREE_END = 242379, 242379 + 1600

def read_lba(img, lba, n):
    out = bytearray()
    with open(img, "rb") as f:
        for i in range(math.ceil(n / UDS)):
            f.seek((lba + i) * SECTOR_SIZE); out += f.read(SECTOR_SIZE)[UDO:UDO + UDS]
    return bytes(out[:n])

def bcd(v): return ((v // 10) << 4) | (v % 10)
def sector_header(lba):
    ab = lba + 150; m, r = divmod(ab, 75 * 60); s, fr = divmod(r, 75)
    return bytes((bcd(m), bcd(s), bcd(fr), 2))
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
        chunk = payload[i * UDS:(i + 1) * UDS]
        track.seek((start_lba + i) * SECTOR_SIZE)
        track.write(make_sector(start_lba + i, chunk, i == cnt - 1))
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
    raise ValueError("디렉터리 엔트리 없음: " + iso_name)
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

def main(outname):
    new_war = open(f"{EXB}/runtime/EX/EX.WAR", "rb").read()
    assert len(new_war) == EX_WAR_SZ, f"EX.WAR 크기 변경됨: {len(new_war)}"
    RELOC = [("E_SCE.BIN;1", 777512, f"{EXB}/rebuilt/EX/E_SCE.BIN"),
             ("BMESS4.BIN;1", 657036, f"{EXB}/rebuilt/BMESS4.BIN"),
             ("E_DEAD.BIN;1", 4828, f"{EXB}/rebuilt/EX/E_DEAD.BIN")]
    out = f"{FULL}/{outname}"
    if os.path.exists(out): os.remove(out)
    shutil.copyfile(BASE, out)
    manifest = []
    with open(out, "r+b") as track:
        # 1) EX.WAR 제자리 패치
        src = read_lba(out, EX_WAR_LBA, EX_WAR_SZ)
        patch_one_executable(track, EX_WAR_LBA, src, new_war, "EX/EX.WAR")
        manifest.append(dict(iso="EX.WAR", mode="in-place", lba=EX_WAR_LBA, size=len(new_war)))
        # 2) 커진 데이터 파일 재배치
        limit = FREE_START * SECTOR_SIZE
        nxt = FREE_START
        for iso, old_size, path in RELOC:
            pay = open(path, "rb").read(); cnt = math.ceil(len(pay) / UDS)
            assert nxt + cnt <= FREE_END, f"{iso}: 빈 영역 초과"
            write_file_at(track, nxt, pay)
            old = retarget(track, iso, nxt, len(pay), old_size, limit)
            manifest.append(dict(iso=iso, mode="reloc", old_lba=old[0], new_lba=nxt,
                                 size=len(pay), sectors=cnt))
            nxt += cnt
    stem = outname[:-4] if outname.endswith(".bin") else outname
    cue = (f'FILE "{outname}" BINARY\r\n  TRACK 01 MODE2/2352\r\n    INDEX 01 00:00:00\r\n'
           f'FILE "Super Robot Taisen Complete Box (Track 2).bin" BINARY\r\n'
           f'  TRACK 02 AUDIO\r\n    INDEX 00 00:00:00\r\n    INDEX 01 00:02:00\r\n')
    open(f"{FULL}/{stem}.cue", "wb").write(cue.encode())
    print(f"OUT {out} ({os.path.getsize(out)} B)")
    for m in manifest: print("  ", m)
    print("  free used:", nxt - FREE_START, "of", FREE_END - FREE_START, "sectors")
    return out, manifest

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "CB_ex_test1.bin")
