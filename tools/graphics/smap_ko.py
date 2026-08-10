# -*- coding: utf-8 -*-
"""SMAP 아카이브(CB `C_SMAP.BIN` / 제2차 단독판 `Z_SMAP.BIN`)의 한글 그래픽 주입.

같은 그래픽이 두 디스크에 들어 있는데 **파일 오프셋만 다르다**. 그래서 픽셀
오프셋을 박아 두지 않고 **TIM 헤더의 VRAM 좌표+크기**로 찾는다.

    타이틀 메뉴   멤버 25 전체        (CB 0xCD394 / 단독판 0xCD391)
    오프닝 프롤로그 멤버 34 의 두 번째 스트림 (CB 0x148D5D / 단독판 0x14A8B0)

주의할 것 세 가지 (전부 실제로 밟았다)
  * 압축 스트림은 **4바이트 정렬이 아니다** — 전수 스캔은 step=1 로.
  * 멤버 표에 길이 0 짜리 빈 항목이 섞여 번호가 밀린다.
  * 한 멤버에 스트림이 여러 개일 수 있다. 각 스트림을 **자기 자리 안에서**
    재압축해야 표·파일 크기·다른 멤버 위치가 그대로 남는다.
"""
import struct

import title_menu_strips as TM
import prologue_strips as PS
from srw_lz_fast import decompress
from srw_lz_enc import compress

# VRAM 좌표(x, y, 폭, 높이) -> 한국어. 좌표는 CB·단독판이 같다.
MENU_BY_VRAM = {
    (384, 256, 64, 24): "시작",
    (400, 256, 48, 24): "로드",
    (412, 256, 104, 24): "이어하기",
    (384, 280, 64, 24): "시작",
    (400, 280, 48, 24): "로드",
    (412, 280, 104, 24): "이어하기",
    (384, 304, 72, 32): "시작",
    (402, 304, 56, 32): "로드",
    (416, 304, 112, 32): "이어하기",
}
PROLOGUE_BY_VRAM = {
    (768, 256, 256, 128): 0,
    (768, 384, 256, 64): 1,
    (768, 448, 256, 32): 2,
    (768, 480, 256, 32): 3,
    (832, 256, 256, 48): 4,
    (832, 304, 256, 32): 5,
    (832, 336, 256, 64): 6,
    (832, 464, 256, 32): 7,
}


def tims(a):
    """멤버 안의 표준 TIM 목록. (오프셋, bpp, 폭, 높이, x, y, 픽셀시작, 픽셀끝)"""
    out = []
    i = 0
    while i < len(a) - 8:
        if struct.unpack_from("<I", a, i)[0] == 0x10:
            fl = struct.unpack_from("<I", a, i + 4)[0]
            if fl in (0, 1, 2, 3, 8, 9, 10, 11):
                p = i + 8
                ok = True
                if fl & 8:
                    bl = struct.unpack_from("<I", a, p)[0]
                    if not (12 <= bl <= 0x10000) or p + bl > len(a):
                        ok = False
                    else:
                        p += bl
                if ok:
                    bl = struct.unpack_from("<I", a, p)[0]
                    if 12 <= bl <= 0x40000 and p + bl <= len(a):
                        x, y, w, h = struct.unpack_from("<HHHH", a, p + 4)
                        if w and h and w <= 1024 and h <= 512 and 12 + w * h * 2 == bl:
                            bpp = [4, 8, 16, 24][fl & 3]
                            out.append((i, bpp, w * (16 // bpp) if bpp < 16 else w, h,
                                        x, y, p + 12, p + bl))
                            i = p + bl
                            continue
        i += 4
    return out


def palette(buf, tim_off):
    q = tim_off + 8
    csz, cx, cy, cw, ch = struct.unpack_from("<IHHHH", buf, q)
    cl = q + 12
    return [(((v & 31) << 3), (((v >> 5) & 31) << 3), (((v >> 10) & 31) << 3))
            for v in (struct.unpack_from("<H", buf, cl + 2 * i)[0] for i in range(cw))]


def _patch_stream(data, start, limit, redraw, label, log):
    """[start, limit) 안의 스트림을 풀어 redraw() 를 적용하고 제자리 재압축."""
    raw, used = decompress(data[start:limit], 0)
    body = bytearray(raw)
    before = tims(raw)
    touched = redraw(body, before)
    assert tims(bytes(body)) == before, f"{label}: TIM 목록이 바뀜 — 헤더를 침범했다"
    stray = [i for i in range(len(raw)) if raw[i] != body[i]
             and not any(lo <= i < hi for lo, hi in touched)]
    assert not stray, f"{label}: 지정 범위 밖 변경 {len(stray)}바이트"
    new = compress(bytes(body))
    assert len(new) <= used, f"{label}: 재압축 {len(new)} > 원본 {used} — 자리 부족"
    chk, u2 = decompress(new, 0)
    assert chk == bytes(body) and u2 == len(new), f"{label}: 재압축 왕복 실패"
    log(f"  {label} 재압축 {used:,} -> {len(new):,} (여유 {used-len(new):,}B)")
    return new, used


def redraw_menu(body, tl):
    """타이틀 메뉴 9장."""
    touched = []
    for (off, bpp, w, h, x, y, p0, p1) in tl:
        ko = MENU_BY_VRAM.get((x, y, w, h))
        if ko is None or bpp != 4:
            continue
        stride = (p1 - p0) // h
        src = TM.read_strip(body, p0, stride, w, h)
        bg = TM.background(src)
        TM.write_strip(body, p0, stride, TM.build(ko, src, bg, palette(body, off)))
        touched.append((p0, p1))
    assert len(touched) == len(MENU_BY_VRAM), \
        f"메뉴 TIM 을 {len(touched)}/{len(MENU_BY_VRAM)} 개만 찾음"
    return touched


def redraw_prologue(body, tl):
    """프롤로그 글판 8장."""
    font = PS.global_font(256 - PS.MARGIN * 2, 16 - 3)[0]
    touched = []
    for (off, bpp, w, h, x, y, p0, p1) in tl:
        idx = PROLOGUE_BY_VRAM.get((x, y, w, h))
        if idx is None or bpp != 4:
            continue
        stride = (p1 - p0) // h
        lines = PS.TEXT[idx][4]
        PS.write_block(body, p0, stride, PS.build(lines, w, h, font))
        touched.append((p0, p1))
    assert len(touched) == len(PROLOGUE_BY_VRAM), \
        f"프롤로그 TIM 을 {len(touched)}/{len(PROLOGUE_BY_VRAM)} 개만 찾음"
    return touched


def members(d):
    n = struct.unpack_from("<I", d, 0)[0] // 4
    t = [struct.unpack_from("<I", d, 4 * i)[0] for i in range(n)]
    return [(t[2 * i] + 8 * i, t[2 * i + 1] + 8 * i + 4) for i in range(n // 2)]


def find_stream(d, ms, probe_first_bytes=None, member=None, at=None):
    """스트림 시작 오프셋. member 안에서 at 이 주어지면 그대로 쓴다."""
    s, e = ms[member]
    return (at if at is not None else s), e


def patch_smap(d, menu_at, prologue_at, log=print):
    """메뉴 + 프롤로그를 넣은 새 바이트열을 돌려준다(길이 불변)."""
    ms = members(d)
    out = bytearray(d)
    for at, redraw, label in ((menu_at, redraw_menu, "타이틀 메뉴"),
                              (prologue_at, redraw_prologue, "프롤로그")):
        idx = next(i for i, (s, e) in enumerate(ms) if s <= at < e)
        limit = ms[idx][1]
        new, used = _patch_stream(d, at, limit, redraw, label, log)
        out[at:at + len(new)] = new        # 남는 꼬리는 원본 그대로
    assert len(out) == len(d), "파일 크기 변동"
    return bytes(out)
