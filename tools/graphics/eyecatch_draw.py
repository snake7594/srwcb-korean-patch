# -*- coding: utf-8 -*-
"""예고 타이틀 카드의 일본어 스트립을 한글로 다시 그린다.

스트립은 **크기를 바꾸지 않는다**(폭·높이 그대로). 리소스 표·다른 스트립 위치·
멤버 압축 자리까지 전부 원본 그대로 두기 위해서다. 그래서 한글은 주어진
가로폭 안에 들어가는 가장 큰 글꼴로 그린다.

원본은 획 가장자리에 인덱스 2~11 을 섞어 쓴 안티에일리어스지만, 색을 알 수
없으므로(팔레트가 런타임에 따로 올라온다) **깊이별 최빈 인덱스**만 흉내 낸다.
결과적으로 속=1, 테두리 1픽셀=3 인 깔끔한 2색이 되는데, 원본보다 압축이 잘
돼서 재압축을 원래 자리에 넣을 여유까지 같이 생긴다.
"""
from PIL import Image, ImageDraw, ImageFont

import eyecatch as EC

FONT = "C:/Windows/Fonts/HANDotum.ttf"
FONT_FALLBACK = "C:/Windows/Fonts/malgun.ttf"
MIN_SIZE = 11
MARGIN = 1


def _font(path, size):
    return ImageFont.truetype(path, size)


def _measure(f, text):
    img = Image.new("L", (1024, 256), 0)
    ImageDraw.Draw(img).text((8, 8), text, font=f, fill=255)
    bb = img.getbbox()
    return (0, 0) if not bb else (bb[2] - bb[0], bb[3] - bb[1])


SS = 4                  # 4배로 그린 뒤 줄인다 — 바로 그리면 획이 끊긴다
CORE_RATIO = 0.30       # 획 중 '속'(깊이 2 이상)이 차지해야 할 최소 비율


def _dilate(mask):
    h, w = len(mask), len(mask[0])
    out = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if mask[y][x]:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        if 0 <= y + dy < h and 0 <= x + dx < w:
                            out[y + dy][x + dx] = True
    return out


def _core_ratio(mask):
    d = EC.depth_map([[1 if v else 0 for v in row] for row in mask])
    one = sum(1 for row in d for v in row if v >= 1)
    two = sum(1 for row in d for v in row if v >= 2)
    return (two / one) if one else 1.0


MIN_SQUEEZE = 0.68      # 가로로 이보다 더 눌리면 글자 크기를 줄인다


def _ink(text, path, size, box):
    f = ImageFont.truetype(path, size)
    big = Image.new("L", box, 0)
    ImageDraw.Draw(big).text((12, 8), text, font=f, fill=255)
    bb = big.getbbox()
    return big.crop(bb) if bb else None


def _draw_line(text, avail_w, avail_h):
    """한 줄을 (avail_w, avail_h) 에 꽉 채워 그린 고해상 이미지.

    **높이를 먼저 채우고 모자란 가로만 눌러 준다.** 폭에 맞춰 글자 크기를 줄이면
    칸이 좁은 제목(원문이 4글자인데 한글은 6글자인 경우)만 유독 작아 보인다.
    """
    W, H = avail_w * SS, avail_h * SS
    box = (max(W * 3, 600), H * 3)
    for path in (FONT, FONT_FALLBACK):
        try:
            ImageFont.truetype(path, 12)
        except Exception:
            continue
        # 글꼴 크기는 잉크 높이보다 훨씬 크다(한글은 대략 0.72배). 칸 높이를
        # 꽉 채우려면 크기를 그만큼 위에서부터 훑어야 한다.
        for size in range(int(H * 1.7) + 8, MIN_SIZE * SS, -2):
            cut = _ink(text, path, size, box)
            if cut is None:
                return None
            if cut.height > H:
                continue
            if cut.width <= W:
                return cut
            if cut.width * MIN_SQUEEZE <= W:      # 조금만 누르면 들어간다
                return cut.resize((W, cut.height), Image.LANCZOS)
    raise SystemExit(f"'{text}' 를 {avail_w}x{avail_h} 에 넣을 글꼴 크기를 못 찾음")


def _mask(lines, w, h, pad):
    """줄 목록을 폭 w, 높이 h 안에 채운 불리언 마스크.

    `pad` 는 글자 바깥에 두를 테두리 두께다. 그만큼 여백을 남겨야 테두리가
    스트립 밖으로 잘리지 않는다.
    """
    n = len(lines)
    lh = h // n
    canvas = Image.new("L", (w * SS, h * SS), 0)
    for i, ln in enumerate(lines):
        if not ln:
            continue
        # 좁은 칸에서는 띄어쓰기를 빼면 글자를 더 크게 쓸 수 있다.
        # 원문(일본어)에는 띄어쓰기가 없으니 원본 크기에 가까워진다.
        cands = [c for c in (ln, ln.replace(" ", "")) if c]
        cut = None
        for c in dict.fromkeys(cands):
            got = _draw_line(c, w - 2 * pad, lh - 2 * pad)
            if got is not None and (cut is None or got.height > cut.height * 1.12):
                cut = got
        if cut is None:
            continue
        gw, gh = cut.size
        x = (w * SS - gw) // 2
        y = i * lh * SS + max(0, (lh * SS - gh) // 2)
        canvas.paste(cut, (max(pad * SS, x), min(y, (h - pad) * SS - gh)))
    small = canvas.resize((w, h), Image.LANCZOS)
    a = small.load()
    return [[a[x, y] > 96 for x in range(w)] for y in range(h)]


# 원본에서 확인한 배색. 제목 스트립은 흰 속(1) + 어두운 테두리 한 겹(3),
# '第'/'話' 는 흰 속(1) + 두 겹(안 14, 바깥 15).
# `learn()` 은 자동으로 뽑아 주지만 잡음이 섞인 스트립에서 엉뚱한 값을 내므로
# 그리기에는 쓰지 않는다(분석용으로만 남겨 둠).
TITLE_SCHEME = (1, [3])
HEAD_SCHEME = (1, [15, 14])


def _ink_box(grid):
    """0 이 아닌 픽셀의 바깥 사각형 (x0, y0, x1, y1). 없으면 None."""
    h, w = len(grid), len(grid[0])
    xs = [x for x in range(w) if any(grid[y][x] for y in range(h))]
    ys = [y for y in range(h) if any(grid[y][x] for x in range(w))]
    return (xs[0], ys[0], xs[-1], ys[-1]) if xs else None


def redraw(buf, rec, text, scheme=TITLE_SCHEME):
    """레코드 자리에 한글 text 를 그려 넣는다. text 의 '\\n' 이 줄바꿈.

    **원본 글자가 차지하던 사각형 안에** 그린다. 칸(w x h) 전체에 맞춰 그리면
    글자 크기를 칸에 맞춘 뒤 테두리를 바깥에 덧그리는 만큼 넘쳐서, 위아래가
    잘려 보인다(2026-08-10 제보). 원본 잉크 상자를 목표로 삼으면 잘림도 없고
    화면에서 원본과 같은 자리에 앉는다.

    칸 전체는 먼저 0(투명)으로 지운다 — 일본어 원문이 한 픽셀도 안 남는다.
    """
    core, rings = scheme
    w, h = rec["w"], rec["h"]
    box = _ink_box(EC.read_sprite(bytes(buf), rec)) or (0, 0, w - 1, h - 1)
    bx0, by0, bx1, by1 = box
    bw, bh = bx1 - bx0 + 1, by1 - by0 + 1
    lines = text.split("\n")
    pad = len(rings)
    for shrink in range(0, 6):                 # 테두리까지 상자에 들어갈 때까지
        mask = _mask(lines, bw, bh - shrink, pad)
        painted = EC.paint_scheme(mask, core, rings)
        ink = _ink_box(painted)
        if ink is None:
            break
        if ink[3] - ink[1] + 1 <= bh and ink[2] - ink[0] + 1 <= bw:
            break
    grid = [[0] * w for _ in range(h)]
    ph, pw = len(painted), len(painted[0])
    oy = by0 + max(0, (bh - ph) // 2)
    for y in range(min(ph, h - oy)):
        for x in range(min(pw, w - bx0)):
            grid[oy + y][bx0 + x] = painted[y][x]
    EC.write_sprite(buf, rec, grid)


def preview(buf, rec, scale=2):
    """레코드를 회색조 PNG 이미지로 (검수용)."""
    g = EC.read_sprite(bytes(buf), rec)
    im = Image.new("L", (rec["w"], rec["h"]))
    p = im.load()
    for y in range(rec["h"]):
        for x in range(rec["w"]):
            p[x, y] = g[y][x] * 17
    return im.resize((rec["w"] * scale, rec["h"] * scale), Image.NEAREST)
