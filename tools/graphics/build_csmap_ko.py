# -*- coding: utf-8 -*-
"""C_SMAP.BIN 을 한글 게임 선택 메뉴가 든 버전으로 재구성한다 (크기·오프셋 완전 보존).

멤버 21 을 풀어 스트립 4개(제2차/제3차/EX/트레이닝 모드)를 한글로 다시 그리고
같은 코덱으로 재압축한다. 재압축 결과가 원본 멤버보다 작으므로 **원래 바이트 범위
안에** 넣고 남는 꼬리는 원본 바이트를 그대로 둔다(디코더는 종료 마커에서 멈춘다).

이렇게 하면 표도 파일 크기도 다른 멤버의 위치도 전부 그대로다. 처음엔 멤버가
127바이트 커져 뒤 멤버가 전부 밀렸고, 그 결과 타이틀 로고와 메뉴 창이 사라졌다.
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
import struct, sys, os
SP = str(_P.BUILD)   # 산출물은 작업 폴더에
sys.path.insert(0, SP)
from srw_lz_fast import decompress
from srw_lz_enc import compress
import menu_strips as M
import title_menu_strips as TM
import prologue_strips as PS

SRC = str(_P.EXTRACTED / "C_SMAP.BIN")
OUT = f"{SP}/gfx/C_SMAP_ko.BIN"
os.makedirs(f"{SP}/gfx", exist_ok=True)
TARGET = 21
# 제2차 타이틀 메뉴(시작/로드/이어하기). 표에 길이 0 짜리 빈 항목이 섞여 있어
# 번호가 한 칸씩 밀린다 — 25 가 파일 0xCD394 짜리 멤버다.
TITLE_TARGET = 25
# 오프닝 프롤로그: 멤버 34 는 스트림이 두 개다. 두 번째(파일 0x148D5D)가 글판이다.
PROLOGUE_MEMBER = 34
PROLOGUE_AT = 0x148D5D


def members(d):
    n = struct.unpack_from("<I", d, 0)[0] // 4
    t = [struct.unpack_from("<I", d, 4 * i)[0] for i in range(n)]
    return n, [(t[2 * i] + 8 * i, t[2 * i + 1] + 8 * i + 4) for i in range(n // 2)]


def tims(a):
    """멤버 안의 표준 TIM 목록. 헤더가 하나라도 깨지면 이후가 통째로 사라지므로
    패치 전후로 이 목록이 같은지 반드시 확인한다."""
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
                    if not (12 <= bl <= 0x10000) or p + bl > len(a): ok = False
                    else: p += bl
                if ok:
                    bl = struct.unpack_from("<I", a, p)[0]
                    if 12 <= bl <= 0x40000 and p + bl <= len(a):
                        x, y, w, h = struct.unpack_from("<HHHH", a, p + 4)
                        if w and h and w <= 1024 and h <= 512 and 12 + w * h * 2 == bl:
                            bpp = [4, 8, 16, 24][fl & 3]
                            out.append((i, bpp, w * (16 // bpp) if bpp < 16 else w, h,
                                        x, y, p + 12, p + bl))
                            i = p + bl; continue
        i += 4
    return out


def _palette_at(buf, timlist, px_off):
    """픽셀 오프셋이 속한 TIM 의 CLUT 를 RGB 리스트로."""
    import struct as _s
    for (off, bpp, w, h, x, y, p0, p1) in timlist:
        if p0 == px_off:
            q = off + 8
            csz, cx, cy, cw, ch = _s.unpack_from("<IHHHH", buf, q)
            cl = q + 12
            return [(((v & 31) << 3), (((v >> 5) & 31) << 3), (((v >> 10) & 31) << 3))
                    for v in (_s.unpack_from("<H", buf, cl + 2 * i)[0] for i in range(cw))]
    raise SystemExit(f"CLUT 을 못 찾음: {px_off:#x}")


def main():
    d = open(SRC, "rb").read()
    n, ms = members(d)
    s, e = ms[TARGET]
    raw, used = decompress(d[s:e], 0)
    assert used == e - s, "멤버21 소비 길이 불일치"
    body = bytearray(raw)

    before = tims(raw)
    print(f"  TIM {len(before)}개: " + ", ".join(f"{t[2]}x{t[3]}@{t[0]}" for t in before))
    # 각 스트립이 실제 TIM 픽셀 블록과 정확히 일치하는지 먼저 못 박는다
    byoff = {t[6]: t for t in before}
    for name, off, wb, rows, ko in M.STRIPS:
        t2 = byoff.get(off)
        assert t2 and t2[1] == 4 and t2[2] == wb * 2 and t2[3] == rows, \
            f"{name}: TIM 픽셀 블록과 불일치 (off={off})"
        assert t2[7] == off + wb * rows, f"{name}: 픽셀 끝 불일치"

    for name, off, wb, rows, ko in M.STRIPS:
        src = M.read_strip(body, off, wb, rows)
        band = M.text_band(src)
        px, size = M.build(ko, wb * 2, rows, band)
        M.write_strip(body, off, wb, px)
        print(f"  {name} @{off} {wb*2}x{rows} band={band} <- '{ko}' ({size}px)")

    after = tims(bytes(body))
    assert after == before, "TIM 목록이 바뀜 — 헤더를 침범했다"
    ranges = [(o, o + wb * r) for _, o, wb, r, _ in M.STRIPS]
    stray = [i for i in range(len(raw)) if raw[i] != body[i]
             and not any(lo <= i < hi for lo, hi in ranges)]
    assert not stray, f"스트립 밖 변경 {len(stray)}바이트 (예: {stray[:5]})"
    print(f"  TIM 무결성 OK ({len(after)}개 동일), 스트립 밖 변경 0바이트")

    new = compress(bytes(body))
    room = e - s
    assert len(new) <= room, f"재압축 {len(new)} > 원본 {room} — 자리 부족"
    chk, u2 = decompress(new, 0)
    assert chk == bytes(body) and u2 == len(new), "재압축 왕복 실패"
    print(f"  멤버21 재압축 {room:,} -> {len(new):,} (여유 {room-len(new):,}B, 자리 그대로)")

    out = bytearray(d)
    out[s:s + len(new)] = new          # 남는 꼬리는 원본 바이트 유지

    # ── 멤버 24: 제2차 타이틀 화면 메뉴 3항목 x 3상태 ────────────────────
    ts, te = ms[TITLE_TARGET]
    traw, tused = decompress(d[ts:te], 0)
    assert tused == te - ts, "멤버24 소비 길이 불일치"
    tbody = bytearray(traw)
    tbefore = tims(traw)
    for name, off, stride, w, h, ko in TM.STRIPS:
        src = TM.read_strip(tbody, off, stride, w, h)
        bg = TM.background(src)
        pal = _palette_at(tbody, tbefore, off)
        TM.write_strip(tbody, off, stride, TM.build(ko, src, bg, pal))
        print(f"  {name} @{off:#x} {w}x{h} <- '{ko}'")
    assert tims(bytes(tbody)) == tbefore, "멤버24 TIM 목록이 바뀜"
    tranges = [(o, o + st * h) for _, o, st, _w, h, _k in TM.STRIPS]
    tstray = [i for i in range(len(traw)) if traw[i] != tbody[i]
              and not any(lo <= i < hi for lo, hi in tranges)]
    assert not tstray, f"멤버24 스트립 밖 변경 {len(tstray)}바이트"
    tnew = compress(bytes(tbody))
    troom = te - ts
    assert len(tnew) <= troom, f"멤버24 재압축 {len(tnew)} > {troom} — 자리 부족"
    chk2, u4 = decompress(tnew, 0)
    assert chk2 == bytes(tbody) and u4 == len(tnew), "멤버24 재압축 왕복 실패"
    print(f"  멤버24 재압축 {troom:,} -> {len(tnew):,} (여유 {troom-len(tnew):,}B)")
    out[ts:ts + len(tnew)] = tnew

    # ── 멤버 34 두 번째 스트림: 오프닝 프롤로그 글판 8장 ──────────────────
    ps, pe = ms[PROLOGUE_MEMBER]
    praw, pused = decompress(d[PROLOGUE_AT:pe], 0)
    proom = pused                      # 이 스트림이 원래 쓰던 바이트 수
    pbody = bytearray(praw)
    pbefore = tims(praw)
    font = PS.global_font(256 - PS.MARGIN * 2, 16 - 3)[0]
    for off, stride, w, h, lines in PS.TEXT:
        PS.write_block(pbody, off, stride, PS.build(lines, w, h, font))
    assert tims(bytes(pbody)) == pbefore, "프롤로그 TIM 목록이 바뀜"
    pranges = [(o, o + st * h) for o, st, _w, h, _l in PS.TEXT]
    pstray = [i for i in range(len(praw)) if praw[i] != pbody[i]
              and not any(lo <= i < hi for lo, hi in pranges)]
    assert not pstray, f"프롤로그 글판 밖 변경 {len(pstray)}바이트"
    pnew = compress(bytes(pbody))
    assert len(pnew) <= proom, f"프롤로그 재압축 {len(pnew)} > {proom} — 자리 부족"
    chk3, u6 = decompress(pnew, 0)
    assert chk3 == bytes(pbody) and u6 == len(pnew), "프롤로그 재압축 왕복 실패"
    print(f"  프롤로그 재압축 {proom:,} -> {len(pnew):,} (여유 {proom-len(pnew):,}B), 글판 8장")
    out[PROLOGUE_AT:PROLOGUE_AT + len(pnew)] = pnew

    outb = bytes(out)
    assert len(outb) == len(d), "파일 크기 변동"
    os.makedirs(f"{SP}/gfx", exist_ok=True)
    open(OUT, "wb").write(outb)

    # 검증 ① 표·다른 멤버 바이트가 전부 그대로
    n2, ms2 = members(outb)
    assert n2 == n and ms2 == ms, "표가 바뀜"
    for i, (a, b) in enumerate(ms):
        if i in (TARGET, TITLE_TARGET, PROLOGUE_MEMBER): continue
        assert outb[a:b] == d[a:b], f"멤버 {i} 변경됨"
    # 검증 ② 대상 멤버가 패치된 픽셀로 해제되고, 소비 길이가 원본 범위 안
    o2, u3 = decompress(outb[s:e], 0)
    assert o2 == bytes(body) and u3 == len(new) <= room
    o4, u5 = decompress(outb[ts:te], 0)
    assert o4 == bytes(tbody) and u5 == len(tnew) <= troom
    o6, u7 = decompress(outb[PROLOGUE_AT:pe], 0)
    assert o6 == bytes(pbody) and u7 == len(pnew) <= proom
    # 멤버34 의 **첫 번째** 스트림은 손대지 않았는지
    assert outb[ps:PROLOGUE_AT] == d[ps:PROLOGUE_AT], "멤버34 첫 스트림이 변경됨"
    print(f"  검증 통과: 파일 {len(outb):,}B (원본과 동일), 멤버 {len(ms)}개 위치 불변")
    print(f"WROTE {OUT}")


if __name__ == "__main__":
    main()
