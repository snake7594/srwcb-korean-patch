# -*- coding: utf-8 -*-
"""v0.10.5 이미지에 메뉴 정렬 교정본(THIRD/EX/TR)을 제자리로 얹어 v0.10.6 을 만든다.

menu_align_fix.py 가 세 실행파일의 ui_master 메뉴 정렬을 제2차 기준으로 교정했다.
세 파일 모두 크기 불변(제자리+도너)이라 원래 LBA 에 덮어쓰고 EDC/ECC 만 재계산한다.

최종 게이트:
  1) 각 교정본이 ISO 항목 크기와 동일
  2) 모든 ui_master/테이블 참조 글리프가 살아있는(비어있지 않은) 폰트 슬롯을 가리킴
     — 도너로 회수한 슬롯을 참조하면 프리즈/깨짐 (제3차 v3 프리즈 유형)
  3) 이미지에서 다시 읽어 바이트 일치, 파일 크기 불변, 변경 파일은 셋뿐
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
import math, os, shutil, struct, sys, hashlib
from pathlib import Path

ROOT = str(_P.WORK)
SP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(_P.TOOLS)); sys.path.insert(0, SP)
from patch_raw_track_exes import (SECTOR_SIZE, USER_DATA_OFFSET as UDO,
                                  USER_DATA_SIZE as UDS, rebuild_mode2_form1)
from extract_psx_iso import RawMode2Image, read_tree
from patch_second_exe_ui import parse_second_ui_vm_record as PV

BASE = f"{ROOT}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.5 (Track 1).bin"
OUT = f"{ROOT}/test_build/third_full/Super Robot Taisen Complete Box Korean v0.10.6 (Track 1).bin"
GB, GC = 32, 2816
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}

FIXED = {
    "THIRD.WAR": dict(war=f"{SP}/tr/fix/THIRD.war", font=0x2872C, MH=0x247CC,
                      tables=[(0xbb0c, 144), (0xbf68, 94), (0xc634, 64), (0xc9ac, 1408),
                              (0x1130c, 52), (0x1155c, 22), (0x11668, 192), (0x10dbf8, 400),
                              (0x10eb2c, 400), (0x110208, 448)]),
    "EX.WAR": dict(war=f"{SP}/tr/fix/EX.war", font=0x1D544, MH=0x188C4,
                   tables=[(0xbcb4, 144), (0xc184, 94), (0xc850, 64), (0xcbcc, 1344),
                           (0xf258, 52), (0xf510, 22), (0xf61c, 192), (0x10778c, 400),
                           (0x1081bc, 400), (0x108f64, 448)]),
    "TR.WAR": dict(war=f"{SP}/tr/fix/TR.war", font=0x1D520, MH=0x188BC,
                   tables=[(0xbcac, 144), (0xc17c, 94), (0xc848, 64), (0xcbc4, 1344),
                           (0xf250, 52), (0xf508, 22), (0xf614, 192), (0x107768, 400),
                           (0x108198, 400), (0x108f40, 448)]),
}


def rec_end_dlg(b, s):
    p = s
    while p < len(b):
        if b[p] == 0xFF:
            return p + 1
        p += 1 if b[p] < 0xEB else (2 if b[p] <= 0xF5 else 1 + CTRL.get(b[p], 0))
    return s


def glyphs_dlg(b, s, e):
    out = []; p = s
    while p < e - 1:
        x = b[p]
        if x < 0xEB: out.append(x); p += 1
        elif x <= 0xF5: out.append(((x - 0xEB) << 8) | b[p + 1]); p += 2
        else: p += 1 + CTRL.get(x, 0)
    return out


def glyph_gate(war, cfg, name):
    """모든 참조 글리프가 비어있지 않은 폰트 슬롯인지."""
    font = cfg["font"]
    def empty(g):
        o = font + g * GB
        return not any(war[o:o + GB])
    used = set()
    for ptr, cnt in cfg["tables"]:
        for k in range(cnt):
            f = ptr + 4 + 4 * k
            t = f + struct.unpack_from("<i", war, f)[0]
            if 0x800 <= t < len(war):
                used.update(glyphs_dlg(war, t, rec_end_dlg(war, t)))
    MH = cfg["MH"]
    for k in range(107):
        f = MH + 4 + 4 * k
        t = f + struct.unpack_from("<i", war, f)[0]
        _, toks = PV(bytes(war), t)
        for tk in toks:
            if tk.kind == 'glyph':
                r = tk.raw
                used.add(r[0] if len(r) == 1 else ((r[0] - 0xEB) << 8) | r[1])
    dead = sorted(g for g in used if g != 0x3FF and g >= 0x101 and empty(g))
    if dead:
        raise SystemExit(f"{name}: 빈 글리프 슬롯 참조 {len(dead)}종: {[hex(x) for x in dead[:12]]}")
    return len(used)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def main():
    with RawMode2Image(Path(BASE)) as m:
        _, E = read_tree(m)
    P = {e.path.strip("/").split("/")[-1]: e for e in E}

    # 게이트 1·2
    payloads = {}
    for iso, cfg in FIXED.items():
        war = bytearray(open(cfg["war"], "rb").read())
        e = P[iso]
        assert len(war) == e.size, f"{iso}: 크기 {len(war)} != ISO {e.size}"
        n_used = glyph_gate(war, cfg, iso)
        payloads[iso] = (e, bytes(war))
        print(f"{iso}: {len(war):,}B  글리프 참조 {n_used}종 모두 유효")

    if os.path.exists(OUT):
        os.remove(OUT)
    shutil.copyfile(BASE, OUT)
    total_sec = 0
    with open(OUT, "r+b") as t:
        for iso, (e, war) in payloads.items():
            cnt = math.ceil(len(war) / UDS); changed = 0
            for i in range(cnt):
                t.seek((e.lba + i) * SECTOR_SIZE)
                sec = bytearray(t.read(SECTOR_SIZE))
                chunk = war[i * UDS:(i + 1) * UDS]
                if sec[UDO:UDO + len(chunk)] == chunk:
                    continue
                sec[UDO:UDO + len(chunk)] = chunk
                rebuild_mode2_form1(sec)
                t.seek((e.lba + i) * SECTOR_SIZE); t.write(sec)
                changed += 1
            total_sec += changed
            print(f"  {iso}: {changed} 섹터 갱신 (LBA {e.lba})")

    # 게이트 3: 재확인
    with open(OUT, "rb") as f:
        for iso, (e, war) in payloads.items():
            got = bytearray()
            for i in range(math.ceil(len(war) / UDS)):
                f.seek((e.lba + i) * SECTOR_SIZE)
                got += f.read(SECTOR_SIZE)[UDO:UDO + UDS]
            assert bytes(got[:len(war)]) == war, f"{iso}: 재확인 실패"
    assert os.path.getsize(OUT) == os.path.getsize(BASE), "이미지 크기 변동"
    print(f"\n총 {total_sec} 섹터 갱신, 파일 크기 불변")
    print(f"OUT {OUT}")
    print(f"sha {sha(OUT)[:16]}")


if __name__ == "__main__":
    main()
