# -*- coding: utf-8 -*-
"""텍스트 VM 의 0xF8(치환) 패딩 루프에 하한 검사를 넣는다.

## 왜

`F8 <인자>` 는 런타임 값을 문자열에 끼워 넣는 옵코드다. 인자가 0x80 보다 크면
**폭 W = 인자 - 0x80** 이 스크립트에 박히고, 실제 길이는 파라미터 링버퍼에서
꺼낸다. 핸들러는 이렇게 동작한다(EX 0x80069D60 기준, 네 실행파일 동일):

    a2 = W                      # 정적, 스크립트에 박힘
    a1 = 링버퍼에서 꺼낸 길이     # 런타임
    v1 = a2 - a1
    if v1 != 0:  do { *p++ = 0x00; } while (--v1)      # 빈칸 패딩
    v1 = a1;     링버퍼에서 a1 바이트 복사

`v1` 은 부호 없는 32비트로 도는데 **하한 검사가 없다.** 치환값이 W 보다 길면
`v1` 이 음수가 되어 `2^32 - n` 번 돌며 RAM 을 0으로 밀어 버린다 — 그 자리에서
게임이 멈춘다. 2026-08-20 v0.11.34 의 유닛 개조 프리징이 정확히 이 경로였다
([[srwcb-ui-string-table]]).

원본 코드가 이 검사를 빼먹었다는 근거는 **같은 엔진 안에 있다.** 링버퍼에
값을 밀어 넣는 생산자 쪽(EX 0x8006C088)은 똑같은 뺄셈 뒤에 `blez` 로 음수를
막는다. 소비자만 `beqz` 다. 일본어 원문은 반각(1바이트/글자)이라 폭을 넘길 일이
드물어 드러나지 않았을 뿐이다. 한글은 전각(2바이트/글자)이라 여지가 절반이다.

## 무엇을 바꾸나

    beqz $v1, <끝>   ->   blez $v1, <끝>      (opcode 0x04 -> 0x06)

`a2 >= a1` 이면 동작이 **완전히 동일**하다. 다르게 도는 건 레트일이라면 멈췄을
경우뿐이라, 잃는 동작이 없다. 명령 하나의 opcode 필드만 바꾸므로 크기·주소가
움직이지 않는다.

이건 개별 문안을 고치는 게 아니라 **프리징 한 부류 전체를 막는** 안전망이다.
문안 쪽 폭 위반은 따로 `audit/verify_subst_width.py` 가 잡는다.
"""
import os
import struct
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)

#: 패딩 루프의 지문. `subu $v1,$a2,$a1` 로 카운터를 만든 직후의 두 갈래
#: (`flags & 8` 로 갈린다)를 각각 잡는다. 두 갈래 모두 같은 `v1` 을 쓴다.
#:
#:   0x00C51823  subu  $v1, $a2, $a1
#:   0x1060xxxx  beqz  $v1, +xxxx          <- 이 자리를 blez 로
#:   0x00000000  nop
#:   0xA0800000  sb    $zero, ($a0)
#:   0x2463FFFF  addiu $v1, $v1, -1
#:   0x1460FFFD  bnez  $v1, -3
_SUBU_V1_A2_A1 = 0x00C51823
_PAD_LOOP = (0xA0800000, 0x2463FFFF)          # sb $zero,($a0) / addiu $v1,$v1,-1

BEQZ_OP = 0x04
BLEZ_OP = 0x06


def _is_beqz_v1(w):
    return (w >> 26) == BEQZ_OP and ((w >> 21) & 0x1F) == 3 and ((w >> 16) & 0x1F) == 0


def _to_blez(w):
    return (w & 0x03FFFFFF) | (BLEZ_OP << 26)


def load_base(buf):
    """PS-X EXE 머리글의 t_addr. 실행파일마다 로드 주소가 다르다."""
    if buf[:8] != b"PS-X EXE":
        return 0x80010000
    return struct.unpack_from("<I", buf, 0x18)[0]


def find_sites(buf, base=None, foff=0x800):
    if base is None:
        base = load_base(buf)
    """(파일오프셋, 주소) 목록 — 고쳐야 할 `beqz $v1` 자리."""
    n = (len(buf) - foff) // 4
    words = struct.unpack_from(f"<{n}I", buf, foff)
    subus = [i for i, w in enumerate(words) if w == _SUBU_V1_A2_A1]
    out = []
    for i in subus:
        # subu 직후 8개 명령 안에서 `beqz $v1` 을 찾되, 그 분기가 실제로
        # 패딩 루프(sb $zero / addiu $v1,-1)를 감싸고 있어야 한다.
        for j in range(i + 1, min(i + 13, n - 3)):
            if not _is_beqz_v1(words[j]):
                continue
            if words[j + 2:j + 4] != _PAD_LOOP:
                continue
            out.append((foff + j * 4, base + j * 4))
    return out


def harden(buf, name="", log=None):
    """제자리 패치. (새 bytes, 고친 개수)"""
    b = bytearray(buf)
    sites = find_sites(b)
    for off, addr in sites:
        w = struct.unpack_from("<I", b, off)[0]
        struct.pack_into("<I", b, off, _to_blez(w))
        if log:
            log(f"    {name} 0x{addr:08X}  beqz $v1 -> blez $v1")
    return bytes(b), len(sites)


#: 텍스트 VM 이 들어 있는 실행파일
EXES = ("SECOND/SECOND.WAR", "THIRD/THIRD.WAR", "EX/EX.WAR", "TR.WAR", "SLPS_020.70")


def apply(files: dict, log=print) -> int:
    total = 0
    for name in EXES:
        if name not in files:
            continue
        new, n = harden(files[name], name, log=None)
        if n:
            files[name] = new
            total += n
            log(f"  {name}: 치환 패딩 루프 {n}곳에 하한 검사")
        else:
            log(f"  [경고] {name}: 치환 패딩 루프를 못 찾음")
    return total


def verify(buf) -> int:
    """아직 `beqz` 로 남아 있는 취약 자리 수 (0이어야 한다)."""
    return len(find_sites(buf))


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    raw = a.path.read_bytes()
    for off, addr in find_sites(raw):
        print(f"  0x{addr:08X} (파일 0x{off:X})")
    new, n = harden(raw, a.path.name, log=print)
    print(f"{n}곳")
    if a.out:
        a.out.write_bytes(new)
        print(f"WROTE {a.out}")
