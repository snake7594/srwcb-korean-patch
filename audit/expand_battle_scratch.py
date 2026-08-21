# -*- coding: utf-8 -*-
"""전투 텍스트 조립 스크래치를 넓힌다 — 한글이 레트일 슬롯(256B)을 넘긴다.

## 왜

전투 메시지 평가기는 선택된 잎(leaf)들을 **하나의 스크래치 버퍼**에 이어 붙인다.
잎 하나가 기여하는 것은 `화자 접두(FF 제외) + F6 + BMESS 레코드(FF 포함)` 이고,
마지막에 목록 종결 FF 하나가 더 붙는다. 그런데 레트일은 모드 0~3 에게 각각
**0x100(256)바이트만 주고 경계 검사를 전혀 하지 않는다.**

    lui  $v1, 0x8018        스크래치 베이스 0x80182C1C
    ori  $v1, $v1, 0x2C1C
    sll  $v0, $a0, 8        <- 모드 인덱스 * 0x100

0x80182C1C 는 모듈 끝보다 한참 뒤, 즉 **malloc 힙 안**이다. 넘치면 맵·유닛·스프라이트
같은 힙 데이터가 덮인다. CPU 는 멀쩡히 돌기 때문에 음악은 계속 나오고 화면만
망가진다 — 커서가 사라지고 입력이 안 먹는 식이다.

일본어는 가나가 반각(1바이트/글자)이라 아슬아슬하게 들어갔다. 한글은 전각(2바이트)이라
같은 문장이 대략 1.5배가 된다.

    모드당 최대 사용 실측
      제2차  레트일 257 -> 한글 366     (v0.8 대 부터 확장 적용 중)
      제3차  레트일 261 -> 한글 382     <- 미적용. 126바이트 넘침
      EX     레트일  89 -> 한글 126     여유 있음
      TR     레트일  89 -> 한글 126     여유 있음

## 무엇을 하나

`tools/build_second_expanded_patch.py` 가 제2차에 하는 수술을 그대로 일반화했다.
모듈 끝 뒤에 `0x200 * 4 = 0x800` 바이트를 예약하고 스크래치를 거기로 옮긴다.

    1) 스크래치 베이스 -> 모듈 끝,  stride sll 8 -> sll 9 (256 -> 512)
    2) 오버레이 머리글의 모듈 끝 워드(파일 0x800)를 +0x800
    3) BSS 클리어 종료 주소($v1 lui/addiu)를 +0x800
    4) 힙 베이스($a0 lui/addiu)를 +0x800

네 실행파일 모두 같은 모양이고, 제2차의 하드코딩 상수와 정확히 일치하는 것을 확인했다.

    SECOND  모듈끝 0x8015BE70  스크래치코드 0xC3020  BSS 0x44354  힙 0x4439C
    THIRD   모듈끝 0x8015CC08  스크래치코드 0xC3890  BSS 0x44A04  힙 0x44A4C
    EX      모듈끝 0x80154B18  스크래치코드 0xBCF8C  BSS 0x3DCA8  힙 0x3DCF0
    TR      모듈끝 0x80154AF0  스크래치코드 0xBCF68  BSS 0x3DC84  힙 0x3DCCC
"""
import os
import struct
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools", "image-build"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

MODULE_END_WORD = 0x800
SLOT_COUNT = 4
RETAIL_SLOT = 0x100
NEW_SLOT = 0x200
HEAP_CEILING = 0x801F8000
MINIMUM_HEAP = 0x60000

#: `lui $v1,0x8018 ; ori $v1,$v1,0x2C1C ; sll $v0,$a0,8` — 네 실행파일 바이트 동일
SCRATCH_SOURCE = bytes.fromhex("188003 3C1C2C6334001204 00".replace(" ", ""))


def _lui_ori(reg, addr):
    return struct.pack("<II",
                       0x3C000000 | (reg << 16) | ((addr >> 16) & 0xFFFF),
                       0x34000000 | (reg << 21) | (reg << 16) | (addr & 0xFFFF))


def _lui_addiu(reg, addr):
    high = ((addr + 0x8000) >> 16) & 0xFFFF
    return struct.pack("<II",
                       0x3C000000 | (reg << 16) | high,
                       0x24000000 | (reg << 21) | (reg << 16) | (addr & 0xFFFF))


def _one(buf, pat, what):
    n = buf.count(pat)
    if n != 1:
        raise ValueError(f"{what}: 자리가 {n}곳 (1곳이어야 한다)")
    return buf.find(pat)


def sites(buf):
    """(스크래치코드, BSS클리어, 힙베이스, 모듈끝) 파일 오프셋과 주소."""
    module_end = struct.unpack_from("<I", buf, MODULE_END_WORD)[0]
    if not 0x80010000 < module_end < HEAP_CEILING:
        raise ValueError(f"모듈 끝이 이상하다: {module_end:#x}")
    return {
        "scratch": _one(buf, SCRATCH_SOURCE, "전투 스크래치 코드"),
        "bss_clear": _one(buf, _lui_addiu(3, module_end), "BSS 클리어 종료"),
        "heap_base": _one(buf, _lui_addiu(4, module_end), "힙 베이스"),
        "module_end": module_end,
    }


def expand(buf, name=""):
    """제자리 확장. (새 bytes, 새 스크래치 베이스) — 이미 확장돼 있으면 (buf, None)."""
    b = bytearray(buf)
    if SCRATCH_SOURCE not in b:
        return bytes(b), None                     # 이미 패치됨(또는 해당 없음)
    s = sites(b)
    old_end = s["module_end"]
    new_end = old_end + NEW_SLOT * SLOT_COUNT
    heap_start = new_end + 4
    if HEAP_CEILING - heap_start < MINIMUM_HEAP:
        raise ValueError(f"{name}: 힙이 {HEAP_CEILING - heap_start:#x} 로 줄어 최소치 미달")

    b[s["scratch"]:s["scratch"] + 8] = _lui_ori(3, old_end)
    #   sll $v0, $a0, 9  (모드 인덱스 * 0x200)
    struct.pack_into("<I", b, s["scratch"] + 8, 0x00041240)
    b[s["bss_clear"]:s["bss_clear"] + 8] = _lui_addiu(3, new_end)
    b[s["heap_base"]:s["heap_base"] + 8] = _lui_addiu(4, new_end)
    struct.pack_into("<I", b, MODULE_END_WORD, new_end)

    # 되읽어 확인
    if struct.unpack_from("<I", b, MODULE_END_WORD)[0] != new_end:
        raise AssertionError(f"{name}: 모듈 끝 기록 실패")
    if SCRATCH_SOURCE in b:
        raise AssertionError(f"{name}: 스크래치 코드가 아직 레트일 그대로다")
    return bytes(b), old_end


def is_expanded(buf):
    """확장이 적용돼 있으면 True (레트일 스크래치 코드가 없으면 적용된 것)."""
    return SCRATCH_SOURCE not in buf


def slot_bytes(buf):
    """이 실행파일이 모드당 주는 스크래치 바이트."""
    return RETAIL_SLOT if not is_expanded(buf) else NEW_SLOT


#: (실행파일, 전투 아카이브, 화자명표 파일오프셋) — 측정에 필요한 짝.
#: 화자명표는 실행파일마다 자기참조 400엔트리 표가 넷 있는데 평가기가 쓰는 것은
#: 오름차순 두 번째다(제2차 0x10CE10 이 tools/build_second_expanded_patch.py 의
#: 하드코딩 값과 일치하는 것으로 확인).
MEASURED = (
    ("SECOND/SECOND.WAR", "BMESS2.BIN", 0x10CE10),
    ("THIRD/THIRD.WAR", "BMESS3.BIN", 0x10DBFC),
    ("EX/EX.WAR", "BMESS4.BIN", 0x107790),
    ("TR.WAR", "BMESS4.BIN", 0x10776C),
)


def measure(exe_buf, archive_buf, table):
    """이 짝의 모드당 최대 스크래치 사용 바이트."""
    from analyze_second_message_archives import (
        analyze_bmess_runtime_scratch, parse_message_record)
    if struct.unpack_from("<I", exe_buf, table - 4)[0] != 0x8000F800 + table:
        raise ValueError(f"화자명표 자기참조 헤더가 0x{table:X} 에서 안 맞는다")
    lengths = []
    for i in range(400):
        field = table + i * 4
        target = field + struct.unpack_from("<i", exe_buf, field)[0]
        record = parse_message_record(exe_buf, target)
        lengths.append(record.end - record.start - 1)
    return analyze_bmess_runtime_scratch(archive_buf, tuple(lengths))["maximum_bytes"]


def apply(files: dict, log=print) -> int:
    """슬롯을 넘기는 실행파일만 넓힌다. 지금은 넘기는 것이 없어 보통 0이다."""
    done = 0
    for exe, arc, table in MEASURED:
        if exe not in files or arc not in files:
            continue
        used = measure(files[exe], files[arc], table)
        slot = slot_bytes(files[exe])
        if used <= slot:
            continue
        new, old_end = expand(files[exe], exe)
        if old_end is None:
            raise SystemExit(
                f"{exe}: 전투 스크래치 {used}B 가 슬롯 {slot}B 를 넘는데 "
                f"이미 확장돼 있어 더 넓힐 수 없다")
        files[exe] = new
        done += 1
        log(f"  {exe}: {used}B > {slot}B — 스크래치 0x{old_end:08X} 로 이전 (256B -> 512B)")
    return done


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    raw = a.path.read_bytes()
    s = sites(raw)
    print(f"모듈 끝 0x{s['module_end']:08X}  스크래치코드 0x{s['scratch']:X}  "
          f"BSS 0x{s['bss_clear']:X}  힙 0x{s['heap_base']:X}")
    new, old = expand(raw, a.path.name)
    print(f"새 스크래치 베이스 0x{old:08X}" if old else "변경 없음")
    if a.out:
        a.out.write_bytes(new)
        print(f"WROTE {a.out}")
