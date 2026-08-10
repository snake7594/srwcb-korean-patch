# -*- coding: utf-8 -*-
"""제2차 오프닝(위로 흐르는 프롤로그) 텍스트를 한글로 다시 그린다.

자산 위치 (2026-08-10, 세이브스테이트 VRAM 역검색으로 확정)
    `C_SMAP.BIN` **멤버 34 안의 두 번째 스트림** (파일 0x148D5D, 해제 133,312B)
    그 안에 표준 TIM 17개. 그중 8개가 프롤로그 글판이다.

글판은 전부 폭 256, **줄 높이 16px**, 4bpp 이고 팔레트가 아주 단순하다.

    인덱스 2 = 배경(검정)   0 = 글자(흰색)   1 = 글자 윤곽(거의 검정)

화면 순서는 VRAM Y 좌표가 아니라 **이야기 순서**다(아래 TEXT 의 나열 순서).
"""
from PIL import Image, ImageDraw, ImageFont

BG, BODY, EDGE = 2, 0, 1
MARGIN = 2                      # 원본 글자가 x=2 에서 시작한다
FONT = "C:/Windows/Fonts/HANDotum.ttf"
FONT_FALLBACK = "C:/Windows/Fonts/malgun.ttf"

# (픽셀 오프셋, stride 바이트, 폭, 높이, [줄...])
TEXT = [
    (0x012800, 128, 256, 128, [
        "초인적인 두뇌와 뛰어난 결단력을 지닌",
        "한 과학자가 있었다.",
        "그는 치밀한 조사 끝에, 외우주에서 지구로",
        "중대한 위기가 다가오고 있음을 알고,",
        "가진 힘을 모두 쏟아부어",
        "한 대의 로봇을 만들어 냈다.",
        "그 로봇의 이름은 발시온‥‥‥",
        "절대적인 힘을 자랑하는 궁극의 로봇이다.",
    ]),
    (0x016840, 128, 256, 64, [
        "하지만 그, 비안 졸다크는",
        "한 가지 불안을 느끼고 있었다.",
        "먼저 인류를 통일해 총력을 모으지 않으면",
        "우주의 침략은 막을 수 없지 않을까‥‥‥.",
    ]),
    (0x018880, 128, 256, 32, [
        "비안 박사는 지구를 지키기 위한 비밀 결사",
        "디바인 크루세이더즈, 통칭 DC를 결성했다.",
    ]),
    (0x0198c0, 128, 256, 32, [
        "DC의 목적은 단 하나.",
        "힘에 의한 세계 통일‥‥‥ 즉 세계 정복이었다",
    ]),
    (0x01aa40, 128, 256, 48, [
        "DC는 순식간에 세계를 제압해 나갔다.",
        "최강이라 불리던 국제연합군조차",
        "발시온 앞에서는 무력했다.",
    ]),
    (0x01c280, 128, 256, 32, [
        "하지만 전 세계의 8할이 DC에 지배당하게",
        "되어도, 저항을 계속하는 사람들이 있었다.",
    ]),
    (0x01e680, 128, 256, 64, [
        "그들은 각지에서 DC에 맞서 게릴라전을 벌였다",
        "이윽고 건담, 마징가Z, 겟타로보",
        "세 로봇을 중심으로 힘을 모아",
        "DC의 지배에 맞서 공공연히 반기를 들었다.",
    ]),
    (0x01d400, 128, 256, 32, [
        "그리고 지금, 전 세계를 뒤흔들",
        "제2차 슈퍼로봇대전이 시작되려 하고 있다‥‥",
    ]),
]


def read_block(data, off, stride, w, h):
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            b = data[off + y * stride + x // 2]
            row.append(b & 0x0F if x % 2 == 0 else b >> 4)
        out.append(row)
    return out


def write_block(data, off, stride, px):
    for y, row in enumerate(px):
        for x in range(0, len(row), 2):
            data[off + y * stride + x // 2] = (row[x] & 0x0F) | ((row[x + 1] & 0x0F) << 4)


def global_font(max_w, line_h):
    """프롤로그 **전체**가 같은 크기로 보이도록 글꼴 크기를 하나로 정한다.

    줄마다 따로 맞추면 어떤 줄은 크고 어떤 줄은 작아서 눈에 거슬린다.
    모든 줄이 들어가는 가장 큰 크기를 찾는다.
    """
    lines = [ln for _o, _s, _w, _h, ls in TEXT for ln in ls if ln]
    for path in (FONT, FONT_FALLBACK):
        for size in range(line_h + 2, 7, -1):
            try:
                f = ImageFont.truetype(path, size)
            except Exception:
                break
            ok = True
            for ln in lines:
                img = Image.new("L", (max_w * 3, line_h * 4), 0)
                ImageDraw.Draw(img).text((4, 2), ln, font=f, fill=255)
                bb = img.getbbox()
                if bb and (bb[2] - bb[0] > max_w or bb[3] - bb[1] > line_h):
                    ok = False
                    break
            if ok:
                return f, size
    raise SystemExit("프롤로그 전체가 들어가는 글꼴 크기를 못 찾음 — 번역을 줄이세요")


def _fit_font(line, max_w, max_h):
    """줄 하나가 (max_w, max_h) 안에 들어가는 가장 큰 글꼴을 고른다."""
    for path in (FONT, FONT_FALLBACK):
        for size in range(max_h + 2, 7, -1):
            try:
                f = ImageFont.truetype(path, size)
            except Exception:
                break
            img = Image.new("L", (max_w * 3, max_h * 3), 0)
            ImageDraw.Draw(img).text((4, 2), line, font=f, fill=255)
            bb = img.getbbox()
            if not bb:
                return f, 0, 0
            if bb[2] - bb[0] <= max_w and bb[3] - bb[1] <= max_h:
                return f, bb[2] - bb[0], bb[3] - bb[1]
    raise SystemExit(f"'{line}' 이 {max_w}x{max_h} 에 안 들어감 — 번역을 줄이세요")


def build(lines, w, h, font=None):
    """글판 한 장을 통째로 다시 그린다. 줄 높이는 h/줄수.

    글자는 줄 높이보다 3px 작게 잡는다 — 윤곽 1px 이 위아래로 번지므로 여백이
    없으면 윗줄·아랫줄이 서로 붙어 읽기 나빠진다.
    """
    n = len(lines)
    lh = h // n
    f = font or global_font(w - MARGIN * 2, lh - 3)[0]
    canvas = Image.new("L", (w, h), 0)
    for i, line in enumerate(lines):
        if not line:
            continue
        img = Image.new("L", (w * 3, lh * 4), 0)
        ImageDraw.Draw(img).text((4, 2), line, font=f, fill=255)
        bb = img.getbbox()
        if not bb:
            continue
        canvas.paste(img.crop(bb), (MARGIN, i * lh + max(0, (lh - (bb[3] - bb[1])) // 2)))
    a = canvas.load()
    body = [[a[x, y] > 110 for x in range(w)] for y in range(h)]
    # 원본과 같은 구조: 흰 글자 + 1픽셀 어두운 윤곽
    out = [[BG] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if body[y][x]:
                out[y][x] = BODY
    for y in range(h):
        for x in range(w):
            if out[y][x] != BG:
                continue
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and body[yy][xx]:
                        out[y][x] = EDGE
                        break
                if out[y][x] != BG:
                    break
    return out
