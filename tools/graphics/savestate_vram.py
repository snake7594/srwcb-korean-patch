# -*- coding: utf-8 -*-
"""DuckStation 세이브스테이트에서 VRAM/RAM 을 꺼내고 그림으로 렌더한다.

기록된 절차(메모리 srwcb-graphics-codec)를 도구로 굳힌 것:
  세이브스테이트(zstd 프레임) → 청크 목록 → GPU(VRAM 1MB) / Bus(RAM 2MB)
  → VRAM 을 4bpp·8bpp·16bpp 로 렌더 → 스프라이트를 눈으로 찾음
  → 그 픽셀 행을 게임파일 해제 결과에서 역검색하면 출처가 나온다.

사용:
    python savestate_vram.py <state.sav> <출력폴더>
"""
import struct
import sys
from pathlib import Path


def frames(raw: bytes):
    """세이브스테이트 안의 zstd 프레임들을 풀어서 돌려준다."""
    from compression import zstd
    magic = b"\x28\xb5\x2f\xfd"
    out = []
    i = raw.find(magic)
    while i >= 0:
        try:
            d = zstd.decompress(raw[i:])
        except Exception:
            d = None
        if d and len(d) > 0x10000:
            out.append((i, d))
        i = raw.find(magic, i + 4)
    return out


def chunks(blob: bytes):
    """[u32 len][name][data] 청크 목록 → {이름: (오프셋, 길이)}."""
    found = {}
    p = 0
    while p + 4 < len(blob) and len(found) < 64:
        n = struct.unpack_from("<I", blob, p)[0]
        if 1 <= n <= 32 and p + 4 + n < len(blob):
            name = blob[p + 4:p + 4 + n]
            if all(32 <= c < 127 for c in name):
                found[name.decode()] = p + 4 + n
                p += 4 + n
                continue
        p += 1
    return found


def render(vram: bytes, out: Path, bpp: int, width: int, rows: int):
    """VRAM 을 지정 색심도로 PNG 로 렌더한다(팔레트 없이 명암만)."""
    from PIL import Image
    if bpp == 4:
        px = bytearray()
        for b in vram[:rows * width // 2]:
            px.append((b & 0x0F) * 17)
            px.append((b >> 4) * 17)
        img = Image.frombytes("L", (width, len(px) // width), bytes(px))
    elif bpp == 8:
        img = Image.frombytes("L", (width, rows), vram[:rows * width])
    else:
        px = bytearray()
        for i in range(0, min(len(vram), rows * width * 2), 2):
            w = vram[i] | (vram[i + 1] << 8)
            px += bytes(((w & 31) << 3, ((w >> 5) & 31) << 3, ((w >> 10) & 31) << 3))
        img = Image.frombytes("RGB", (width, len(px) // (width * 3)), bytes(px))
    img.save(out)
    return out


def main():
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])
    dst.mkdir(parents=True, exist_ok=True)
    raw = src.read_bytes()
    fr = frames(raw)
    print(f"zstd 프레임 {len(fr)}개: {[(hex(o), len(d)) for o, d in fr]}")
    body = max(fr, key=lambda z: len(z[1]))[1]
    (dst / "state_body.bin").write_bytes(body)
    ch = chunks(body)
    print("청크:", {k: hex(v) for k, v in ch.items()})
    # GPU 청크 안에 VRAM 1MB
    # 'GPU' 는 레지스터 청크다. 실제 1MB 프레임버퍼는 'GPU-VRAM'.
    gpu = ch.get("GPU-VRAM") or ch.get("GPU")
    if gpu is None:
        print("[없음] GPU-VRAM 청크 — 수동으로 찾으세요")
        return
    vram = body[gpu:gpu + 0x100000 + 0x400]
    (dst / "vram.bin").write_bytes(vram)
    print(f"VRAM 후보 {len(vram):,}B → vram.bin")
    for off in (0, 0x10, 0x20, 0x40, 0x80):
        try:
            render(vram[off:], dst / f"vram_4bpp_{off:03x}.png", 4, 4096, 512)
        except Exception as ex:
            print("렌더 실패", off, ex)
    print("렌더 완료:", sorted(p.name for p in dst.glob("*.png")))


if __name__ == "__main__":
    main()
