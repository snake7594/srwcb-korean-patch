# -*- coding: utf-8 -*-
"""v0.10.6 이미지에 프리즈-수정된 2_SCE.BIN(이벤트 스크립트 포인터 재조준)을
제자리로 얹어 v0.10.7 을 만든다.

2_SCE 는 크기 불변(disp만 재조준)이라 원래 LBA 에 덮어쓰고 EDC/ECC 만 재계산한다.
게이트: ISO 목록 동일, 변경 파일은 2_SCE 하나뿐, 재조준 후 모든 이벤트 참조가
레코드 시작을 가리킴.
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
import math, os, shutil, struct, sys, hashlib
from pathlib import Path

ROOT = str(_P.WORK)
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(_P.TOOLS)); sys.path.insert(0, SP)
from patch_raw_track_exes import (SECTOR_SIZE, USER_DATA_OFFSET as UDO,
                                  USER_DATA_SIZE as UDS, rebuild_mode2_form1)
from extract_psx_iso import RawMode2Image, read_tree
import fix_sce_event_refs as FX

BASE = f"{ROOT}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.6 (Track 1).bin"
OUT = f"{ROOT}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.7 (Track 1).bin"
NEW = f"{SP}/sce_ko_fixed.bin"
JP = f"{ROOT}/extracted/SECOND/2_SCE.BIN"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    payload = open(NEW, "rb").read()
    jp = open(JP, "rb").read()
    # 게이트: 재조준본에 스테일 참조 0
    bad = FX._verify(payload, jp)
    assert bad == 0, f"재조준본에 스테일 참조 {bad}건"
    print(f"게이트 1: 재조준본 이벤트 참조 스테일 0건 OK")

    with RawMode2Image(Path(BASE)) as m:
        _, E = read_tree(m)
    e = next(x for x in E if x.path.strip("/") == "SECOND/2_SCE.BIN")
    assert len(payload) == e.size, f"크기 {len(payload)} != ISO {e.size}"

    if os.path.exists(OUT):
        os.remove(OUT)
    shutil.copyfile(BASE, OUT)
    cnt = math.ceil(len(payload) / UDS); changed = 0
    with open(OUT, "r+b") as t:
        for i in range(cnt):
            t.seek((e.lba + i) * SECTOR_SIZE)
            sec = bytearray(t.read(SECTOR_SIZE))
            chunk = payload[i * UDS:(i + 1) * UDS]
            if sec[UDO:UDO + len(chunk)] == chunk:
                continue
            sec[UDO:UDO + len(chunk)] = chunk
            rebuild_mode2_form1(sec)
            t.seek((e.lba + i) * SECTOR_SIZE); t.write(sec)
            changed += 1
    print(f"2_SCE 제자리 교체: 섹터 {cnt}개 중 {changed}개 갱신 (LBA {e.lba})")

    # 게이트: 이미지에서 다시 읽어 일치, 다른 파일 불변
    with open(OUT, "rb") as f:
        got = bytearray()
        for i in range(cnt):
            f.seek((e.lba + i) * SECTOR_SIZE)
            got += f.read(SECTOR_SIZE)[UDO:UDO + UDS]
    assert bytes(got[:len(payload)]) == payload, "재확인 실패"
    assert os.path.getsize(OUT) == os.path.getsize(BASE), "이미지 크기 변동"

    with RawMode2Image(Path(BASE)) as m:
        _, EB = read_tree(m)
    with RawMode2Image(Path(OUT)) as m:
        _, EO = read_tree(m)
    tb = {x.path.strip("/"): (x.lba, x.size) for x in EB}
    to = {x.path.strip("/"): (x.lba, x.size) for x in EO}
    assert tb == to, "ISO 목록 변동"
    print(f"게이트 2: ISO 목록 동일({len(to)}개), 파일 크기 불변")
    print(f"\nOUT {OUT}\nsha {sha(OUT)[:16]}")


if __name__ == "__main__":
    main()
