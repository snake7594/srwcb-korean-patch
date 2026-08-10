# -*- coding: utf-8 -*-
"""시나리오 예고 타이틀 카드(第N話 …) 그래픽을 다룬다.

자산 위치 (2026-08-10, 세이브스테이트 VRAM 역검색으로 확정)
    `EFFECT.BIN` 의 멤버 **157~172**. 한 멤버가 예고 화면 한 묶음이다.

    | 멤버 | 내용 |
    |---|---|
    | 157~163 | EX (제목만, 영문 부제 없음) |
    | 164~165 | 제2차 26화 |
    | 166~168 | 제3차 51화 |
    | 169~172 | EX 나머지 |

리소스 형식 (게임 자체 포맷. TIM 이 아니다)
    `[BE16 개수][16바이트 레코드 × 개수][CLUT 여러 벌][픽셀 데이터]`
    레코드 = `[BE32 데이터오프셋][BE16 a][BE16 b][BE16 폭][BE16 높이][BE16 px][BE16 py]`
      * a,b 는 VRAM 텍스처 좌표/페이지. 건드리지 않는다.
      * (px,py) 는 **기준점**이다. 일본어 제목은 (폭,높이) = 오른쪽아래 기준
        → 화면 오른쪽에 붙는다. 영문 부제는 (0,0) = 왼쪽위 기준.
    픽셀은 4bpp 인데 **니블이 뒤집혀 있다**(왼쪽 픽셀이 상위 니블).

레코드 0~22 은 어느 멤버에나 있는 공용 글자다.
    0~9 큰 숫자 16x24 / 10 '第' 24x24 / 11 '話' 24x24
    12~21 작은 숫자 / 22 'Scenario:' 80x24

**그리는 규칙은 원본에서 배운다.** 인덱스 팔레트가 이 리소스 안에 없어서(런타임에
따로 올라온다) 색을 알 수 없다. 대신 원본의 **깊이별 인덱스**를 그대로 쓴다.
    제목 스트립: 깊이1 = 3(외곽), 깊이2+ = 1(흰 속)
    '第'/'話' : 깊이1 = 15, 깊이2 = 14, 깊이3+ = 1
"""
import struct

HEAD_RECORDS = 23          # 0~22 은 숫자·第·話·Scenario:
DAI_INDEX = 10             # '第'
WA_INDEX = 11              # '話'


def parse(raw):
    """(레코드 목록, 헤더 끝 오프셋). 레코드 = dict."""
    n = struct.unpack_from(">H", raw, 0)[0]
    out = []
    for i in range(n):
        off, a, b, w, h, px, py = struct.unpack_from(">IHHHHHH", raw, 2 + 16 * i)
        out.append({"i": i, "off": off, "a": a, "b": b, "w": w, "h": h,
                    "px": px, "py": py})
    return out, 2 + 16 * n


def read_sprite(raw, rec):
    """4bpp 스프라이트를 인덱스 2차원 배열로 (왼쪽 픽셀 = 상위 니블)."""
    off, w, h = rec["off"], rec["w"], rec["h"]
    stride = w // 2
    grid = []
    for y in range(h):
        row = []
        base = off + y * stride
        for x in range(w):
            i = base + x // 2
            byte = raw[i] if i < len(raw) else 0
            row.append((byte >> 4) if x % 2 == 0 else (byte & 0x0F))
        grid.append(row)
    return grid


def write_sprite(buf, rec, grid):
    """인덱스 배열을 제자리에 쓴다. 크기는 원본과 같아야 한다."""
    off, w, h = rec["off"], rec["w"], rec["h"]
    assert len(grid) == h and all(len(r) == w for r in grid), "크기가 다르다"
    stride = w // 2
    for y in range(h):
        base = off + y * stride
        for x in range(0, w, 2):
            i = base + x // 2
            if i < len(buf):
                buf[i] = ((grid[y][x] & 0x0F) << 4) | (grid[y][x + 1] & 0x0F)


def title_records(recs):
    """제목 스트립 레코드만. (일본어, 영문) 쌍이면 영문은 py==0 이다."""
    out = []
    for r in recs[HEAD_RECORDS:]:
        if r["w"] > 40 and r["h"] in (24, 40, 48, 64):
            out.append(r)
    return out


def japanese_records(recs):
    """일본어 제목 스트립(기준점이 오른쪽아래인 것)."""
    return [r for r in title_records(recs) if r["py"] == r["h"] and r["px"] == r["w"]]


def depth_map(grid):
    """배경(0)까지의 거리. 1 = 가장자리."""
    h, w = len(grid), len(grid[0])
    d = [[0] * w for _ in range(h)]
    cur = [(y, x) for y in range(h) for x in range(w) if grid[y][x]]
    alive = [[bool(grid[y][x]) for x in range(w)] for y in range(h)]
    lvl = 1
    while cur:
        edge = []
        rest = []
        for y, x in cur:
            if any(not (0 <= y + dy < h and 0 <= x + dx < w) or not alive[y + dy][x + dx]
                   for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1))):
                edge.append((y, x))
            else:
                rest.append((y, x))
        if not edge:
            for y, x in rest:
                d[y][x] = lvl
            break
        for y, x in edge:
            d[y][x] = lvl
            alive[y][x] = False
        cur = rest
        lvl += 1
    return d


def learn(grid):
    """깊이 -> 그 깊이에서 원본이 가장 많이 쓴 인덱스."""
    import collections
    d = depth_map(grid)
    stat = collections.defaultdict(collections.Counter)
    for y in range(len(grid)):
        for x in range(len(grid[0])):
            if grid[y][x]:
                stat[d[y][x]][grid[y][x]] += 1
    return {k: v.most_common(1)[0][0] for k, v in stat.items()}


def ring_scheme(rule):
    """깊이 규칙 -> (속 인덱스, 바깥쪽부터의 테두리 인덱스 목록).

    원본은 '가장자리 몇 겹 + 속' 구조다. 제목 스트립은 `{1:3, 2:1, 3:1}` →
    속 1, 테두리 [3] 한 겹. '第'/'話' 는 `{1:15, 2:14, 3:1, …}` → 속 1,
    테두리 [15, 14] 두 겹(바깥이 15).

    한글은 획이 가늘어 원본처럼 '안쪽을 깎아' 테두리를 만들면 속이 없어진다.
    그래서 **글자 바깥에 테두리를 두르는** 쪽으로 뒤집어 쓴다.
    """
    if not rule:
        return 1, [3]
    seq = [rule.get(d, 1) for d in range(1, max(rule) + 1)]
    core = seq[-1]
    k = len(seq)
    while k > 0 and seq[k - 1] == core:
        k -= 1
    rings = seq[:k] or [seq[0]]
    return core, rings[-2:]        # 테두리는 최대 두 겹 (더 두르면 글자가 묻힌다)


def paint(mask, rule):
    """마스크를 규칙에서 뽑은 배색으로 칠한다."""
    core, rings = ring_scheme(rule)
    return paint_scheme(mask, core, rings)


def paint_scheme(mask, core, rings):
    """마스크를 속 인덱스로 칠하고 바깥에 테두리를 두른다.

    `rings` 는 **바깥에서 안쪽 순서**다. 예: `[15, 14]` 면 글자에 붙은 한 겹이
    14, 그 바깥 한 겹이 15.
    """
    h, w = len(mask), len(mask[0])
    out = [[0] * w for _ in range(h)]
    for y in range(h):
        for x in range(w):
            if mask[y][x]:
                out[y][x] = core
    cur = {(y, x) for y in range(h) for x in range(w) if mask[y][x]}
    seen = set(cur)
    k = len(rings)
    for step in range(1, k + 1):
        nxt = set()
        for (y, x) in cur:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if 0 <= yy < h and 0 <= xx < w and (yy, xx) not in seen:
                        seen.add((yy, xx))
                        nxt.add((yy, xx))
                        out[yy][xx] = rings[k - step]
        cur = nxt
        if not cur:
            break
    return out
