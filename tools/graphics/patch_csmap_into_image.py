# -*- coding: utf-8 -*-
"""v0.10.1 CB 이미지의 C_SMAP.BIN 을 한글 메뉴판으로 제자리 교체한다.

새 C_SMAP 은 원본과 크기가 같으므로 원래 LBA 2081 에 그대로 덮어쓴다.
(파일을 옮기면 뒤 멤버 오프셋이 밀려 타이틀 로고·메뉴 창이 사라진다 — 실측 확인)
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
import math, os, shutil, sys, hashlib

sys.path.insert(0, str(_P.TOOLS))
from patch_raw_track_exes import SECTOR_SIZE, USER_DATA_OFFSET as UDO, USER_DATA_SIZE as UDS, rebuild_mode2_form1

SP = os.path.dirname(os.path.abspath(__file__))
BASE = str(_P.OUT / "cb_korean_prev.bin")
OUT = str(_P.OUT / "cb_korean_next.bin")
NEW = f"{SP}/gfx/C_SMAP_ko.BIN"
LBA, SIZE = 2081, 9932026


def main():
    payload = open(NEW, "rb").read()
    assert len(payload) == SIZE, f"크기 {len(payload)} != {SIZE}"
    if os.path.exists(OUT): os.remove(OUT)
    shutil.copyfile(BASE, OUT)
    cnt = math.ceil(len(payload) / UDS)
    changed = 0
    with open(OUT, "r+b") as t:
        for i in range(cnt):
            t.seek((LBA + i) * SECTOR_SIZE)
            sec = bytearray(t.read(SECTOR_SIZE))
            chunk = payload[i * UDS:(i + 1) * UDS]
            if sec[UDO:UDO + len(chunk)] == chunk:
                continue
            sec[UDO:UDO + len(chunk)] = chunk
            rebuild_mode2_form1(sec)
            t.seek((LBA + i) * SECTOR_SIZE); t.write(sec)
            changed += 1
    print(f"C_SMAP 제자리 교체: {cnt} 섹터 중 {changed} 섹터 갱신 (LBA {LBA})")
    h = hashlib.sha256()
    with open(OUT, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    print("OUT", OUT)
    print("크기", os.path.getsize(OUT), " sha", h.hexdigest()[:16])


if __name__ == "__main__":
    main()
