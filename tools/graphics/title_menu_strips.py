# -*- coding: utf-8 -*-
"""제2차 타이틀 화면 메뉴(スタート / ロード / コンティニュー)를 한글로 다시 그린다.

자산 위치 (2026-08-10, 세이브스테이트 VRAM 역검색으로 확정)
    `C_SMAP.BIN` **멤버 24** (파일 0xCD394~0x1AC22F, 해제 109,792B)
    그 안에 표준 TIM 12개. 메뉴는 3항목 x 3상태 = 9개다.

      상태        CLUT      배경 인덱스   비고
      비선택      (0,492)   0            회색 글자
      강조        (0,491)   12           흰 글자 (모양은 비선택과 동일)
      선택        (0,490)   0            노랑 글자 + 주황 글로우, 캔버스가 더 크다

**픽셀 좌표는 반드시 TIM 헤더에서 얻는다.** 화면 캡처로 역산하면 다음 TIM 의
매직·CLUT 를 덮어써서 그 뒤 그래픽이 통째로 사라진다(v0.10.2 에서 실제로 겪음).

글자 그리는 법은 원본에서 **배웠다**: 픽셀마다 배경까지의 거리(깊이)를 재고,
깊이별로 가장 많이 쓰인 인덱스를 뽑아 그대로 쓴다. 안쪽(코어)뿐 아니라 글자
바깥의 글로우(음수 깊이)도 같은 방식으로 재현된다.
"""
from PIL import Image, ImageDraw, ImageFont

# (이름, 픽셀 오프셋, stride 바이트, 폭, 높이, 한국어)
STRIPS = [
    ("start_dim",  0x1a1e0,  32,  64, 24, "시작"),
    ("load_dim",   0x14800,  24,  48, 24, "로드"),
    ("cont_dim",   0x13680,  52, 104, 24, "이어하기"),
    ("start_lit",  0x1a520,  32,  64, 24, "시작"),
    ("load_lit",   0x14a80,  24,  48, 24, "로드"),
    ("cont_lit",   0x13ba0,  52, 104, 24, "이어하기"),
    ("start_sel",  0x1a860,  36,  72, 32, "시작"),
    ("load_sel",   0x14d00,  28,  56, 32, "로드"),
    ("cont_sel",   0x140c0,  56, 112, 32, "이어하기"),
]

FONT = "C:/Windows/Fonts/HANDotum.ttf"
FONT_FALLBACK = "C:/Windows/Fonts/malgun.ttf"
SHEAR = 0.18          # 원본 가타카나의 이탤릭 기울기


def read_strip(data, off, stride, w, h):
    """4bpp 픽셀을 [행][열] 인덱스 배열로."""
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            b = data[off + y * stride + x // 2]
            row.append(b & 0x0F if x % 2 == 0 else b >> 4)
        out.append(row)
    return out


def write_strip(data, off, stride, px):
    for y, row in enumerate(px):
        for x in range(0, len(row), 2):
            data[off + y * stride + x // 2] = (row[x] & 0x0F) | ((row[x + 1] & 0x0F) << 4)


def background(px):
    """가장 많이 쓰인 인덱스 = 배경."""
    cnt = {}
    for row in px:
        for v in row:
            cnt[v] = cnt.get(v, 0) + 1
    return max(cnt, key=cnt.get)


def _depth_map(mask, h, w):
    """mask(True=글자)에서 각 칸의 깊이. 안쪽은 1,2,3…, 바깥은 0,-1,-2…"""
    INF = 99
    d = [[INF] * w for _ in range(h)]
    # 안쪽 깊이: 배경까지의 체스보드 거리
    for y in range(h):
        for x in range(w):
            if not mask[y][x]:
                d[y][x] = 0
    for _ in range(8):
        changed = False
        for y in range(h):
            for x in range(w):
                if not mask[y][x]:
                    continue
                m = INF
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        yy, xx = y + dy, x + dx
                        v = d[yy][xx] if 0 <= yy < h and 0 <= xx < w else 0
                        m = min(m, v)
                if m + 1 < d[y][x]:
                    d[y][x] = m + 1
                    changed = True
        if not changed:
            break
    # 바깥 링: 글자에서 멀어질수록 -1, -2 …
    out = [[d[y][x] for x in range(w)] for y in range(h)]
    ring = [[0] * w for _ in range(h)]
    cur = {(y, x) for y in range(h) for x in range(w) if mask[y][x]}
    seen = set(cur)
    for k in range(1, 6):
        nxt = set()
        for (y, x) in cur:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and (yy, xx) not in seen:
                        seen.add((yy, xx)); nxt.add((yy, xx)); ring[yy][xx] = -k
        cur = nxt
        if not cur:
            break
    for y in range(h):
        for x in range(w):
            if not mask[y][x]:
                out[y][x] = ring[y][x] if ring[y][x] else None   # None = 순수 배경
    return out


def learn(px, bg):
    """원본에서 깊이 -> 인덱스 규칙을 배운다."""
    h, w = len(px), len(px[0])
    mask = [[px[y][x] != bg for x in range(w)] for y in range(h)]
    dep = _depth_map(mask, h, w)
    tally = {}
    for y in range(h):
        for x in range(w):
            d = dep[y][x]
            if d is None:
                continue
            tally.setdefault(d, {}).setdefault(px[y][x], 0)
            tally[d][px[y][x]] += 1
    return {d: max(c, key=c.get) for d, c in tally.items()}


def render_mask(text, w, h, band):
    """글자를 그려 True/False 마스크로. band=(top,bottom) 원본 글자 세로 범위.

    작은 캔버스에 바로 그리면 획이 끊기고 지저분하다. **4배로 그린 뒤 줄인다.**
    기울이기도 고해상도 단계에서 해야 계단이 안 생긴다.
    """
    SS = 4
    top, bot = band
    band_h = max(8, bot - top + 1)
    avail_w, avail_h = w - 2, band_h
    chosen = None
    for path in (FONT, FONT_FALLBACK):
        for size in range(band_h * SS + 8, 8 * SS, -1):
            try:
                f = ImageFont.truetype(path, size)
            except Exception:
                break
            big = Image.new("L", (w * SS * 2, h * SS * 2), 0)
            d = ImageDraw.Draw(big)
            d.text((10, 10), text, font=f, fill=255)
            bb = big.getbbox()
            if not bb:
                continue
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            if tw <= avail_w * SS and th <= avail_h * SS:
                chosen = big.crop(bb)
                break
        if chosen:
            break
    if chosen is None:
        raise SystemExit(f"'{text}' 를 {avail_w}x{avail_h} 에 넣을 폰트 크기를 못 찾음")

    canvas = Image.new("L", (w * SS, h * SS), 0)
    gw, gh = chosen.size
    canvas.paste(chosen, ((w * SS - gw) // 2, (top + (band_h - gh / SS) / 2) * SS if False
                          else int(top * SS + (band_h * SS - gh) / 2)))
    canvas = canvas.transform((w * SS, h * SS), Image.AFFINE,
                              (1, SHEAR, -SHEAR * h * SS / 2, 0, 1, 0),
                              resample=Image.BILINEAR)
    small = canvas.resize((w, h), Image.LANCZOS)
    a = small.load()
    mask = [[a[x, y] > 100 for x in range(w)] for y in range(h)]
    # 한글은 획이 가늘어 코어가 안 생기면 속 빈 윤곽선이 된다. 윤곽 1픽셀을 빼고도
    # 본체가 남으려면 획이 최소 3픽셀이어야 한다(깊이 2 이상이 두 겹).
    for _ in range(2):
        dep = _depth_map(mask, h, w)
        deep2 = sum(1 for y in range(h) for x in range(w) if (dep[y][x] or 0) >= 2)
        deep1 = sum(1 for y in range(h) for x in range(w) if (dep[y][x] or 0) >= 1)
        if deep1 and deep2 / deep1 >= 0.30:      # 본체가 획의 30% 이상
            break
        mask = _dilate(mask, h, w)
    return mask


def _dilate(mask, h, w):
    out = [[False] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if mask[y][x]:
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        yy, xx = y + dy, x + dx
                        if 0 <= yy < h and 0 <= xx < w:
                            out[yy][xx] = True
    return out


def text_band(px, bg):
    ys = [y for y, row in enumerate(px) if any(v != bg for v in row)]
    return (min(ys), max(ys)) if ys else (0, len(px) - 1)


def body_index(px, bg, pal):
    """글자 본체로 쓸 인덱스 — 충분히 많이 쓰인 것 중 **가장 밝은** 색.

    원본은 획이 굵어 안쪽 깊이가 10 이상까지 가고, 그 깊은 자리에 오히려 어두운
    음영이 들어간다. 한글은 획이 가늘어 깊이가 2~3 에서 끝나므로 '가장 깊은 곳의
    색'을 그대로 쓰면 어두워진다. 그래서 밝기로 고른다.
    """
    cnt = {}
    for row in px:
        for v in row:
            if v != bg:
                cnt[v] = cnt.get(v, 0) + 1
    total = sum(cnt.values()) or 1
    cand = [i for i, c in cnt.items() if c / total >= 0.03 and i < len(pal)]
    if not cand:
        cand = [i for i in cnt if i < len(pal)] or [bg]
    best = max(cand, key=lambda i: sum(pal[i]))
    # 밝기만 보면 선택 상태(노랑 글자)에서 흰 하이라이트를 골라 버려 '선택됨' 느낌이
    # 사라진다. 비슷하게 밝으면서 더 선명한 색이 있으면 그쪽을 쓴다.
    top = sum(pal[best])
    for i in cand:
        s = max(pal[i]) - min(pal[i])
        if sum(pal[i]) >= top * 0.85 and s > max(pal[best]) - min(pal[best]):
            best = i
    return best


def build(text, px, bg, pal=None):
    """원본 스트립 px 를 보고 같은 규칙으로 한글 스트립을 만든다."""
    h, w = len(px), len(px[0])
    rule = learn(px, bg)
    band = text_band(px, bg)
    mask = render_mask(text, w, h, band)
    dep = _depth_map(mask, h, w)
    # 원본은 가타카나라 획이 굵어 윤곽이 2겹까지 간다. 한글에 그대로 쓰면 획이
    # 통째로 윤곽색이 되어 뭉갠다. **윤곽은 1픽셀만** 쓰고 나머지는 본체색으로.
    inner = [d for d in rule if d > 0]
    core = (body_index(px, bg, pal) if pal
            else (rule[min(inner)] if inner else bg))
    edge = rule.get(1, core)
    if edge == core and inner:
        edge = rule[max(inner)]        # 본체와 같으면 윤곽이 안 보인다
    out = [[bg] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            d = dep[y][x]
            if d is None:
                continue
            if d > 0:
                out[y][x] = edge if d == 1 else core
            else:
                out[y][x] = rule.get(d, bg)
    return out
