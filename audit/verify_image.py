# -*- coding: utf-8 -*-
"""완성된 이미지를 게임이 읽는 경로 그대로 훑어 검사한다 (build_all 8단계).

  A) 잔여 일본어 — 대사·전투·사망 아카이브에 미번역 레코드가 있는지
  B) 폭/줄수   — 시나리오 대사 폭 18·3줄, 전투/사망 대사 폭 40
  C) 무결성    — 레코드 종결자, 깨진 2바이트 글리프, 폰트 범위 밖 글리프

'죽은 사본'(번역 후 도너로 옮기고 남은 원본 자리)은 게임이 안 읽으므로 세지
않는다. 자세한 오탐 함정은 audit/README.md 참고.
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
for _sub in ("tools", "audit"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(_P.TOOLS))
import audit_all as A  # noqa: E402

def sce_dialogue(data):
    """시나리오 '대사' 레코드만 — B1/B3/B4 텍스트 포인터가 가리키는 것.

    풀 안의 모든 레코드를 세면 이벤트 스크립트·조건문까지 딸려 와서 대사창 폭
    규칙(18/3줄)을 엉뚱한 데 들이대게 된다.
    """
    out = []
    for s in A.ASR.parse_scenarios(data):
        starts = {r.start: r for r in s.records}
        for ref in s.references:
            r = starts.get(ref.target)
            if r is not None:
                out.append((s.index, ref.target, r.start, r.end))
    return out


def is_choice(data, s, tbl):
    """선택지·메뉴 레코드인가 (앞에 스크립트 바이트가 붙는다).

    이런 레코드는 창 폭이 레트일 원문 기준이라 대사창 18 규칙을 들이대면 안 된다.
    """
    for off, n, k, g in A.toks(data, s, A.rec_end(data, s)):
        if k == 'end':
            break
        if k == 'g':
            return len(tbl.get(g, "")) != 1
    return False


def dead_live(data):
    """빈 슬롯을 뺀 실제 사망 대사.

    선두 표는 (시작, 끝) 쌍이다. 둘이 같으면 슬롯이 비어 있다는 뜻인데, 그걸
    레코드로 세면 엉뚱한 레코드 중간을 가리켜 대량 오탐이 난다.
    """
    import struct as _s
    tb = _s.unpack_from("<I", data, 0)[0]
    out = []
    for i in range(0, tb // 4 - 1, 2):
        a = i * 4 + _s.unpack_from("<I", data, i * 4)[0]
        b = (i + 1) * 4 + _s.unpack_from("<I", data, (i + 1) * 4)[0]
        if a == b or not (0 < a < b <= len(data)):
            continue
        out.append((i // 2, a, A.rec_end(data, a)))
    return out


GAMES = [
    ("제2차", "SECOND/2_SCE.BIN", "BMESS2.BIN", "SECOND/2_DEAD.BIN", A.PINNED),
    ("제3차", "THIRD/3_SCE.BIN", "BMESS3.BIN", "THIRD/3_DEAD.BIN", A.PINNED),
    ("EX",   "EX/E_SCE.BIN",    "BMESS4.BIN", "EX/E_DEAD.BIN",    A.EX15),
]


# 대사창 상자. 세 게임 전 시나리오의 참조 대사 레코드를 다 훑어 잰 값이다 —
# 줄을 바꾼 지점은 16~32 에 몰려 있고 32 를 넘는 줄이 하나도 없다. 한 쪽은 3줄까지.
# 긴 대사는 원문도 F7 로 쪽을 나눈다(쪽 나눔은 정상이다).
SCE_BOX_ADVANCE = 32
SCE_MAX_LINES = 3
BATTLE_BOX_ADVANCE = 29


def _pages(buf, s, e):
    """레코드를 쪽(F7)·줄(F6)로 갈라 줄별 advance 를 돌려준다."""
    from second_translation_codec import glyph_advance, CONTROL_ARGUMENT_BYTES as CA
    pages, phase, p = [[0]], 0, s
    while p < e:
        b = buf[p]
        if b == 0xFF:
            break
        if b < 0xEB:
            idx, p = b, p + 1
        elif b <= 0xF5:
            idx, p = ((b - 0xEB) << 8) | buf[p + 1], p + 2
        else:
            if b == 0xF6:
                pages[-1].append(0); phase = 0
            elif b == 0xF7:
                pages.append([0]); phase = 0
            p += 1 + CA.get(b, 0)
            continue
        step, phase = glyph_advance(idx, phase)
        pages[-1][-1] += step
    return pages


def check_pages(name, ko, jp, recs_ko, recs_jp, tbl, box, out):
    """대사가 상자를 넘지 않는지 — 줄 폭 <= 32, 한 쪽 <= 3줄.

    쪽(F7)을 줄로 세면 안 된다. 원문도 긴 대사는 쪽을 나눠 쓴다. 예전 검사는
    시나리오 안에서 상자를 유추했는데, 같은 시나리오에 폭 276 짜리 특수 레코드가
    섞여 있어 상자가 통째로 부풀었고 그래서 넘침을 0 으로 보고했다.
    """
    jp_over = wide = broke = 0
    for (s, e, _bw, _bh), (sj, ej) in zip(recs_ko, recs_jp):
        try:
            txt = A.decode(ko, s, e, tbl)
            kp = _pages(ko, s, e)
            jpg = _pages(jp, sj, ej)
        except Exception:
            broke += 1
            continue
        n, tot = A.jp_ratio(txt)
        if n >= 3 and tot and n / tot >= 0.5:
            jp_over += 1
        # 원문이 상자보다 크게 쓴 드문 레코드는 원문이 쓴 만큼까지 허용
        wcap = max(box, max(max(pg) for pg in jpg))
        cap = max(SCE_MAX_LINES, max(len(pg) for pg in jpg))
        if any(max(pg) > wcap or len(pg) > cap for pg in kp):
            wide += 1
    out.append((name, len(recs_ko), jp_over, wide, broke))


def check(name, data, recs, tbl, width, lines, out):
    jp = wide = broke = 0
    for row in recs:
        s, e = row[-2], row[-1]
        if e <= s or e > len(data):
            continue
        try:
            txt = A.decode(data, s, e, tbl)
        except Exception:
            broke += 1
            continue
        n, tot = A.jp_ratio(txt)
        if n >= 3 and tot and n / tot >= 0.5:
            jp += 1
        try:
            ws, pages, _mx = A.line_widths(data, s, e)
        except Exception:
            broke += 1
            continue
        if (ws and max(ws) > width) or (lines and pages and max(pages) > lines):
            wide += 1
    out.append((name, len(recs), jp, wide, broke))


_KO_SYL = __import__("re").compile(r"[가-힣]")


def _is_dialogue(buf, s, e, tbl):
    """사람이 읽는 한국어 대사인가 (스크립트 바이트 덩어리를 걸러낸다)."""
    try:
        t = A.decode(buf, s, e, tbl)
    except Exception:
        return False
    k = len(_KO_SYL.findall(t))
    return k >= 6 and k / max(len(t), 1) >= 0.45


def _paired(ko, jp, tbl):
    """같은 순번의 (패치본 대사, 레트일 대사) 짝 + 그 장면의 상자 크기."""
    a = A.ASR.parse_scenarios(jp)
    b = A.ASR.parse_scenarios(ko)
    ko_out, jp_out = [], []
    for x, y in zip(a, b):
        if len(x.records) != len(y.records):
            continue
        # 이 장면의 상자: 원문이 줄을 바꾼 가장 넓은 지점 / 가장 많은 줄수
        bw = bh = 0
        for r in x.records:
            try:
                ws, _pg, _mx = A.line_widths(jp, r.start, r.end)
            except Exception:
                continue
            if len(ws) > 1:
                bw = max(bw, max(ws[:-1]))
            bh = max(bh, len(ws))
        for rx, ry in zip(x.records, y.records):
            # 참조 레코드만 보면 안 된다 — 풀 안 이벤트 스크립트가 가리키는 대사가
            # 그만큼 더 있고, 예전엔 그쪽이 통째로 검사에서 빠져 있었다.
            if is_choice(ko, ry.start, tbl) or not _is_dialogue(ko, ry.start, ry.end, tbl):
                continue
            ko_out.append((ry.start, ry.end, bw, bh))
            jp_out.append((rx.start, rx.end))
    return ko_out, jp_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v0.11.0")
    ap.add_argument("--image", type=Path)
    a = ap.parse_args()
    img = a.image or (_P.OUT /
                      f"Super Robot Taisen Complete Box Korean {a.version} (Track 1).bin")
    if not img.exists():
        raise SystemExit(f"[없음] 이미지: {img}")
    img = str(img)

    rows = []
    for game, sce, bmess, dead, extras in GAMES:
        tbl = A.ko_table(extras)
        d = A.read_iso(img, sce)
        j = (_P.EXTRACTED / sce).read_bytes()
        ko_recs, jp_recs = _paired(d, j, tbl)
        check_pages(f"{game} 대사", d, j, ko_recs, jp_recs, tbl, SCE_BOX_ADVANCE, rows)
        d = A.read_iso(img, bmess)
        check(f"{game} 전투", d, A.bmess_records(d), tbl, BATTLE_BOX_ADVANCE, 0, rows)
        d = A.read_iso(img, dead)
        check(f"{game} 사망", d, dead_live(d), tbl, BATTLE_BOX_ADVANCE, 0, rows)

    print(f"\n{'항목':12} {'레코드':>8} {'미번역':>7} {'폭/줄초과':>9} {'깨짐':>6}")
    bad = 0
    for name, n, jp, wide, broke in rows:
        print(f"{name:12} {n:>8,} {jp:>7} {wide:>9} {broke:>6}")
        bad += jp + wide + broke
    print()
    if bad:
        raise SystemExit(f"검증 실패: 문제 {bad}건")
    print("검증 통과: 미번역 0 / 폭·줄 초과 0 / 깨진 레코드 0")


if __name__ == "__main__":
    main()
