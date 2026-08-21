# -*- coding: utf-8 -*-
"""추적 빌드가 남긴 스크립트 제어흐름 로그를 세이브스테이트에서 읽는다.

`tools/build_ex_trace.py` 가 심은 링버퍼(RAM 0x80154B18)를 DuckStation 세이브스테이트
(`.sav`)에서 꺼내 사람이 읽을 수 있게 푼다. 각 목적지가 어느 시나리오 블록/실행파일
안인지까지 붙여 준다.
"""
import argparse
import io
import struct
import sys
from pathlib import Path

_d = Path(__file__).resolve().parent
while _d != _d.parent and not (_d / "srwcb_paths.py").exists():
    _d = _d.parent
for _s in ("", "image-build", "tools"):
    p = str(_d / _s) if _s else str(_d)
    if p not in sys.path:
        sys.path.insert(0, p)

import srwcb_paths as _P                                    # noqa: E402
import assemble_image as AI                                 # noqa: E402
from analyze_sce_relocation import parse_scenarios          # noqa: E402

LOG = 0x80154B18
RING = 128


def savestate_ram(sav: Path, ex_war: bytes) -> bytes:
    import zstandard
    b = sav.read_bytes()
    i = b.find(bytes.fromhex("28b52ffd"))
    if i < 0:
        raise SystemExit("zstd 프레임을 못 찾음 — DuckStation 세이브스테이트가 맞나?")
    blob = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(b[i:])).read()
    j = blob.find(ex_war[0x800:0x800 + 64])
    if j < 0:
        raise SystemExit("이 세이브스테이트의 EX.WAR 가 이 이미지와 다르다 (다른 판으로 뜬 것)")
    base = j - 0x10000
    return blob[base:base + 0x200000]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sav", type=Path)
    ap.add_argument("--version", default="v0.11.38")
    a = ap.parse_args()
    img = _P.OUT / f"Super Robot Taisen Complete Box Korean {a.version}-trace (Track 1).bin"
    if not img.exists():
        raise SystemExit(f"[없음] 추적 이미지: {img}")
    with AI.RawMode2Image(img) as m:
        _, entries = AI.read_tree(m)
    by = {e.path.strip("/"): e for e in entries}
    war = AI.read_file(img, by["EX/EX.WAR"].lba, by["EX/EX.WAR"].size)
    sce = AI.read_file(img, by["EX/E_SCE.BIN"].lba, by["EX/E_SCE.BIN"].size)
    ram = savestate_ram(a.sav, war)

    def U32(addr):
        return struct.unpack_from("<I", ram, addr - 0x80000000)[0]

    # RAM 에 올라온 시나리오 블록을 찾아 주소에 이름을 붙인다
    loaded = []
    for s in parse_scenarios(sce):
        i = ram.find(sce[s.block_start:s.block_start + 64])
        if i >= 0:
            loaded.append((s.index, 0x80000000 + i,
                           s.pool_end - s.block_start, s.pool_start - s.block_start))

    def where(v):
        for idx, base, total, scr in loaded:
            if base <= v < base + total:
                kind = "스크립트" if v - base < scr else "텍스트풀"
                return f"sc{idx}+0x{v - base:X} ({kind})"
        if 0x80010000 <= v < 0x80133800:
            return f"EX.WAR+0x{v - 0x80010000:X}"
        if v >= 0x80200000 or v < 0x80000000:
            return "★ RAM 밖"
        return "힙(미상)"

    count = U32(LOG)
    print(f"기록 횟수 {count}  (링 {RING}칸)")
    print(f"현재 스크립트 포인터 0x{U32(0x80132E98 + 0x288):08X}  {where(U32(0x80132E98 + 0x288))}")
    print(f"RAM 에 올라온 시나리오: " + ", ".join(f"sc{i}@0x{b:08X}" for i, b, _, _ in loaded))
    if count == 0:
        raise SystemExit("\n로그가 비었다 — 추적 빌드가 아니거나 아직 아무 점프도 안 했다")
    n = min(count, RING)
    print(f"\n최근 {n}건 (오래된 것 -> 최신):\n")
    start = count - n
    for k in range(start, count):
        e = LOG + 0x10 + (k % RING) * 16
        frm, to, depth, op = (U32(e), U32(e + 4), U32(e + 8), U32(e + 12))
        tag = "  <<< 마지막" if k == count - 1 else ""
        print(f"  #{k:5}  0x{frm:08X} {where(frm):28} ->  0x{to:08X} {where(to):28}"
              f"  깊이 {depth:2} 옵코드 {op:#04x}{tag}")


if __name__ == "__main__":
    main()
