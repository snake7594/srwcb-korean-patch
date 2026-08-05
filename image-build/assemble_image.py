# -*- coding: utf-8 -*-
"""레트일 Track 1 에서 한 번에 한글판 이미지를 조립한다.

예전 방식은 v0.8.7 -> v0.9.0 -> v0.9.3 -> ... -> v0.10.8 처럼 이미지를 계속
덮어쓰며 올라갔다. 중간 이미지가 저장소에 없으니 남이 클론해서 따라올 수가
없었다. 여기서는 **레트일 + 빌드된 파일 19개**만으로 최종 이미지를 만든다.

레이아웃
    * 크기가 그대로인 것(실행파일 4개, SLPS, C_SMAP)은 원래 자리에 덮어쓴다.
    * 커진 것 10개는 레트일 이미지 **끝 뒤에** 정해진 순서로 이어 붙인다.
      (NULL.DA 의 익스텐트가 원래 이미지 끝을 넘어가 있어서, 그 뒤는 아무도
       읽지 않는 여유 공간이다.)
    * 디렉터리 레코드의 LBA/크기를 갱신한다(리틀·빅 양쪽).

제3차만 파일 위치를 **실행파일에 박아 두고** 쓴다(디렉터리를 안 본다). 그래서
3_SCE / 3_DEAD 의 LBA 를 SLPS_020.70 과 THIRD.WAR 안에서 다시 써 준다. 어느
오프셋인지는 `data/lba_refs.json` 에 있다. 나머지 파일은 디렉터리를 보고 찾으
므로 손댈 게 없다 (조사로 확인).
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
import argparse
import hashlib
import json
import math
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(_P.TOOLS))
from extract_psx_iso import RawMode2Image, read_tree  # noqa: E402
from patch_raw_track_exes import rebuild_mode2_form1  # noqa: E402

SEC, UDO, UDS = 2352, 24, 2048

# 이미지 끝에 이어 붙이는 순서 (배포본 레이아웃과 동일하게 유지한다).
APPEND_ORDER = [
    "BMESS2.BIN", "SECOND/2_SCE.BIN", "SECOND/2_DEAD.BIN", "SECOND/SECOND.WAR",
    "BMESS3.BIN", "THIRD/3_SCE.BIN", "THIRD/3_DEAD.BIN",
    "EX/E_SCE.BIN", "BMESS4.BIN", "EX/E_DEAD.BIN",
]
# 원래 자리에 덮어쓰는 것 (크기 불변)
IN_PLACE = ["SLPS_020.70", "C_SMAP.BIN", "THIRD/THIRD.WAR", "EX/EX.WAR", "TR.WAR"]

LBA_REFS = _P.REPO / "image-build" / "data" / "lba_refs.json"


def sectors(n: int) -> int:
    return math.ceil(n / UDS)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()[:16]


def read_file(img: Path, lba: int, size: int) -> bytes:
    out = bytearray()
    with open(img, "rb") as f:
        for i in range(sectors(size)):
            f.seek((lba + i) * SEC)
            out += f.read(SEC)[UDO:UDO + UDS]
    return bytes(out[:size])


def dir_record_offsets(img: Path):
    """{iso 경로: (디렉터리 익스텐트 LBA, 익스텐트 안 바이트 오프셋)}"""
    with RawMode2Image(img) as m:
        pvd = m.read_sector(16)
        root_lba = struct.unpack_from("<I", pvd, 156 + 2)[0]
        root_size = struct.unpack_from("<I", pvd, 156 + 10)[0]
    out = {}
    todo = [(root_lba, root_size, "")]
    seen = set()
    while todo:
        lba, size, parent = todo.pop()
        if (lba, size) in seen:
            continue
        seen.add((lba, size))
        data = read_file(img, lba, size)
        pos = 0
        while pos < len(data):
            rl = data[pos]
            if rl == 0:
                pos = (pos // UDS + 1) * UDS
                continue
            nl = data[pos + 32]
            raw = data[pos + 33:pos + 33 + nl]
            name = ("." if raw == b"\x00" else ".." if raw == b"\x01"
                    else raw.decode("ascii", "replace").split(";", 1)[0])
            if name not in (".", ".."):
                path = f"{parent}/{name}" if parent else name
                out[path] = (lba, pos)
                if data[pos + 25] & 0x02:
                    todo.append((struct.unpack_from("<I", data, pos + 2)[0],
                                 struct.unpack_from("<I", data, pos + 10)[0], path))
            pos += rl
    return out


class Writer:
    """섹터 단위로 모아 썼다가 EDC/ECC 를 다시 계산해 기록한다."""

    def __init__(self, path: Path):
        self.f = open(path, "r+b")
        self.path = path

    def close(self):
        self.f.close()

    def put_file(self, lba: int, data: bytes) -> None:
        for i in range(sectors(len(data))):
            chunk = data[i * UDS:(i + 1) * UDS]
            self.f.seek((lba + i) * SEC)
            sec = bytearray(self.f.read(SEC))
            if len(sec) != SEC:
                raise SystemExit(f"이미지 끝을 넘어 씁니다 (LBA {lba + i})")
            sec[UDO:UDO + UDS] = chunk.ljust(UDS, b"\x00")
            rebuild_mode2_form1(sec)
            self.f.seek((lba + i) * SEC)
            self.f.write(sec)

    def patch_dir(self, dir_lba: int, off: int, lba: int, size: int) -> None:
        s = dir_lba + off // UDS
        o = UDO + off % UDS
        self.f.seek(s * SEC)
        sec = bytearray(self.f.read(SEC))
        struct.pack_into("<I", sec, o + 2, lba)
        struct.pack_into(">I", sec, o + 6, lba)
        struct.pack_into("<I", sec, o + 10, size)
        struct.pack_into(">I", sec, o + 14, size)
        rebuild_mode2_form1(sec)
        self.f.seek(s * SEC)
        self.f.write(sec)


def assemble(retail: Path, out: Path, files: dict[str, bytes], quiet=False) -> dict:
    """files: {iso 경로: 내용}. 반환: {iso 경로: (lba, size)}"""
    with RawMode2Image(retail) as m:
        _, entries = read_tree(m)
    ent = {e.path.strip("/"): e for e in entries}
    unknown = set(files) - set(ent)
    if unknown:
        raise SystemExit(f"디스크에 없는 경로: {sorted(unknown)}")

    append = [p for p in APPEND_ORDER if p in files]
    inplace = [p for p in files if p not in append]
    for p in inplace:
        if len(files[p]) > sectors(ent[p].size) * UDS:
            raise SystemExit(f"{p} 가 원래 자리보다 큽니다 — APPEND_ORDER 에 넣으세요")

    end = retail.stat().st_size // SEC
    plan = {}
    lba = end
    for p in append:
        plan[p] = (lba, len(files[p]))
        lba += sectors(len(files[p]))
    for p in inplace:
        plan[p] = (ent[p].lba, len(files[p]))

    # 제3차 로더는 3_SCE/3_DEAD 위치를 실행파일에 박아 둔다 — 새 LBA 로 다시 쓴다.
    refs = json.loads(LBA_REFS.read_text(encoding="utf-8"))
    for exe, targets in refs.items():
        if exe not in files:
            continue
        buf = bytearray(files[exe])
        n = 0
        for target, offsets in targets.items():
            if target not in plan:
                continue
            new = plan[target][0]
            for o in offsets:
                struct.pack_into("<I", buf, int(o), new)
                n += 1
        files[exe] = bytes(buf)
        if not quiet:
            print(f"  {exe}: 하드코딩 LBA {n}곳 갱신")

    if out.exists():
        out.unlink()
    shutil.copyfile(retail, out)
    total = lba
    with open(out, "r+b") as f:            # 이어 붙일 만큼 늘린다
        f.truncate(total * SEC)
        def bcd(v):
            return (v // 10) * 16 + v % 10
        for s in range(end, total):
            blank = bytearray(SEC)
            blank[:12] = bytes.fromhex("00ffffffffffffffffffff00")
            a = s + 150
            blank[12:16] = bytes([bcd(a // 4500), bcd(a // 75 % 60), bcd(a % 75), 2])
            blank[16:24] = bytes([0, 0, 0x08, 0]) * 2
            rebuild_mode2_form1(blank)
            f.seek(s * SEC)
            f.write(bytes(blank))

    w = Writer(out)
    try:
        for p, data in files.items():
            w.put_file(plan[p][0], data)
        dirs = dir_record_offsets(out)
        for p in files:
            if (plan[p][0], plan[p][1]) == (ent[p].lba, ent[p].size):
                continue
            dl, off = dirs[p]
            w.patch_dir(dl, off, plan[p][0], plan[p][1])
    finally:
        w.close()

    with RawMode2Image(out) as m:
        _, e2 = read_tree(m)
    got = {e.path.strip("/"): e for e in e2}
    for p, data in files.items():
        g = got[p]
        assert (g.lba, g.size) == plan[p], f"{p} 디렉터리 갱신 실패"
        assert read_file(out, g.lba, g.size) == data, f"{p} 내용 불일치"
    if not quiet:
        print(f"  섹터 {end:,} -> {total:,}  ({out.stat().st_size:,} B)")
    return plan


def write_cue(image: Path, track2: str = "Super Robot Taisen Complete Box (Track 2).bin"):
    cue = image.with_suffix(".cue")
    cue.write_bytes((f'FILE "{image.name}" BINARY\r\n  TRACK 01 MODE2/2352\r\n'
                     f'    INDEX 01 00:00:00\r\n'
                     f'FILE "{track2}" BINARY\r\n  TRACK 02 AUDIO\r\n'
                     f'    INDEX 00 00:00:00\r\n    INDEX 01 00:02:00\r\n').encode())
    return cue


def main():
    ap = argparse.ArgumentParser(description="레트일 + 빌드 파일 -> 한글판 이미지")
    ap.add_argument("--manifest", type=Path, required=True,
                    help='{"iso 경로": "로컬 파일"} JSON')
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--retail", type=Path, default=None)
    a = ap.parse_args()
    retail = a.retail or _P.disc()
    man = json.loads(a.manifest.read_text(encoding="utf-8"))
    files = {}
    for iso, local in man.items():
        p = Path(local)
        if not p.exists():
            raise SystemExit(f"[없음] {iso} <- {p}")
        files[iso] = p.read_bytes()
        print(f"  {iso:24} {len(files[iso]):>10,} B  {_sha(files[iso])}")
    a.out.parent.mkdir(parents=True, exist_ok=True)
    assemble(retail, a.out, files)
    print(f"OUT {a.out}\n    {write_cue(a.out).name}")


if __name__ == "__main__":
    main()
