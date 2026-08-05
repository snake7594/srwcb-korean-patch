# -*- coding: utf-8 -*-
"""v0.10.4 이미지에 한글화된 TR.WAR 을 제자리로 얹어 v0.10.5 를 만든다.

TR.WAR 은 크기가 그대로(1,193,984B)라 파일을 옮길 필요가 없다.
원래 LBA 에 덮어쓰고 바뀐 섹터의 EDC/ECC 만 다시 만든다.

BASE 이미지가 없으면 레트일 + 릴리즈된 xdelta 로 되살릴 수 있다:
    xdelta.exe -d -s "…Complete Box (Track 1).bin" \
        srwcb-second-third-ex-korean-v0.10.4.xdelta "…Korean v0.10.4 (Track 1).bin"
TR 만 다시 고칠 때는 BASE 를 이미 만든 v0.10.5 이미지로 두고 그대로 덮어써도 된다
(주입은 이미지가 아니라 test_build/ex_full/font_extracted/TR.WAR 에서 출발한다).
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
import math, os, shutil, sys, hashlib
from pathlib import Path

ROOT = str(_P.WORK)
sys.path.insert(0, str(_P.TOOLS))
from patch_raw_track_exes import (SECTOR_SIZE, USER_DATA_OFFSET as UDO,
                                  USER_DATA_SIZE as UDS, rebuild_mode2_form1)
from extract_psx_iso import RawMode2Image, read_tree

BASE = f"{ROOT}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.4 (Track 1).bin"
OUT = f"{ROOT}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.5 (Track 1).bin"
NEW = f"{ROOT}/test_build/tr_full/TR_final.war"


def main():
    payload = open(NEW, "rb").read()
    with RawMode2Image(Path(BASE)) as m:
        _, entries = read_tree(m)
    e = next(x for x in entries if x.path.strip("/") == "TR.WAR")
    assert len(payload) == e.size, f"크기 {len(payload)} != ISO 항목 {e.size}"
    print(f"TR.WAR  LBA {e.lba}  {e.size:,}B  (제자리 교체)")

    if os.path.exists(OUT):
        os.remove(OUT)
    shutil.copyfile(BASE, OUT)
    cnt = math.ceil(len(payload) / UDS)
    changed = 0
    with open(OUT, "r+b") as t:
        for i in range(cnt):
            t.seek((e.lba + i) * SECTOR_SIZE)
            sec = bytearray(t.read(SECTOR_SIZE))
            chunk = payload[i * UDS:(i + 1) * UDS]
            if sec[UDO:UDO + len(chunk)] == chunk:
                continue
            sec[UDO:UDO + len(chunk)] = chunk
            rebuild_mode2_form1(sec)
            t.seek((e.lba + i) * SECTOR_SIZE)
            t.write(sec)
            changed += 1
    print(f"섹터 {cnt}개 중 {changed}개 갱신")

    # 검증: 이미지에서 다시 읽어 동일한지
    with open(OUT, "rb") as f:
        got = bytearray()
        for i in range(cnt):
            f.seek((e.lba + i) * SECTOR_SIZE)
            got += f.read(SECTOR_SIZE)[UDO:UDO + UDS]
    assert bytes(got[:len(payload)]) == payload, "재확인 실패"
    assert os.path.getsize(OUT) == os.path.getsize(BASE), "이미지 크기 변동"

    h = hashlib.sha256()
    with open(OUT, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    print(f"OUT  {OUT}")
    print(f"크기 {os.path.getsize(OUT):,}  sha {h.hexdigest()[:16]}")


if __name__ == "__main__":
    main()
