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
SCE_BOX_ADVANCE = 31   # 32칸을 꽉 채우면 게임에서 넘친다(v0.11.23)
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
    for (s, e, _bw, _bh, isref), (sj, ej) in zip(recs_ko, recs_jp):
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
        # ★ 원문이 안 쓴 쪽 나눔(F7)을 우리가 넣으면 안 된다. 대사창 경로는 F7 을
        #   처리하지 못해서 대사가 꼬이다가 게임이 멈춘다(제3차 8화).
        if (any(max(pg) > wcap or len(pg) > cap for pg in kp)
                or (isref and len(kp) > len(jpg))):
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
    """같은 순번의 (패치본 대사, 레트일 대사) 짝 + 상자 크기 + 대사창 여부."""
    a = A.ASR.parse_scenarios(jp)
    b = A.ASR.parse_scenarios(ko)
    ko_out, jp_out = [], []
    for x, y in zip(a, b):
        if len(x.records) != len(y.records):
            continue
        # 이 장면의 상자: 원문이 줄을 바꾼 가장 넓은 지점 / 가장 많은 줄수
        _refs = {r.target for r in x.references}
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
            ko_out.append((ry.start, ry.end, bw, bh, rx.start in _refs))
            jp_out.append((rx.start, rx.end))
    return ko_out, jp_out


def check_wrap_width():
    """레이아웃 엔진이 **주어진 상자 폭까지** 채우는지 확인한다.

    v0.11.7 까지 emit_text/emit_control 이 상자 폭(self.max_advance) 대신 모듈
    상수 MAX_LINE_ADVANCE(18) 를 보고 줄을 접었다. 대사가 두 배 줄 수로 부풀어
    3줄 상자를 넘쳤고, 제3차 대사창 레코드는 F7(쪽 나눔)을 쓸 수 없어 그대로
    깨졌다(화자가 사라지고 문장 중간부터 나오는 그 증상). 제2차·EX 는 원문이
    F7 을 쓰는 레코드가 많아 쪽이 하나 더 생기고 마는 정도라 눈에 덜 띄었다.
    """
    sys.path.append(str(_P.REPO / "tools"))
    from second_translation_codec import (LayoutState, load_safe_glyph_map,
                                          normalise_for_font)
    gm = load_safe_glyph_map()
    st = LayoutState(gm, max_advance=31, max_lines=3, allow_page_break=False)
    st.emit_text(normalise_for_font("가나다라마바사아자차카타파하거너더러머버서어저처")[0])
    _enc, man = st.finish()
    first = man["page_advances"][0][0]
    if first <= 18:
        raise SystemExit(
            f"[회귀] 레이아웃이 폭 32 상자를 {first} 칸에서 접었습니다 — "
            "emit_text/emit_control 이 self.max_advance 대신 상수를 보고 있습니다")
    return first


def check_objective_block(name, ko, jp, tbl, out):
    """작전목적(승리/패배조건) 블록에 줄을 더 넣지 않았는지.

    시나리오 앞머리의 이 레코드들은 작전목적 화면이 읽는데, 줄(F6)이 하나라도
    늘면 창이 깨지고 게임이 멈춘다(2026-08-09 제보, 제3차 8화). 대사창과 달리
    여기는 **원문이 쓴 줄 수 그대로** 여야 한다.
    """
    sys.path.append(str(_P.REPO / "tools"))
    from analyze_sce_relocation import parse_scenarios, objective_block_records

    def _lines(buf, s, e):
        n, i = 1, s
        while i < e:
            x = buf[i]
            if x == 0xFF:
                break
            if x < 0xEB:
                i += 1
            elif x < 0xF6:
                i += 2
            else:
                if x in (0xF6, 0xF7):
                    n += 1
                i += 1 + {0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2,
                          0xFC: 2, 0xFD: 2, 0xFE: 1}.get(x, 0)
        return n

    block = objective_block_records(jp)
    bad = 0
    for a, b in zip(parse_scenarios(jp), parse_scenarios(ko)):
        if len(a.records) != len(b.records):
            continue
        for ra, rb in zip(a.records, b.records):
            if ra.start not in block:
                continue
            # 스크립트(바이트코드) 레코드는 '줄'이 의미가 없다. 재조준된 변위
            # 바이트가 우연히 0xF6 이 되면 줄이 는 것처럼 보일 뿐이다.
            if not _is_dialogue(jp, ra.start, ra.end, tbl):
                continue
            if _lines(ko, rb.start, rb.end) > _lines(jp, ra.start, ra.end):
                bad += 1
    if bad:
        out.append((f"{name} 작전목적", len(block), 0, bad, 0))


def check_retail_leftovers(name, ko, jp, recs_ko, recs_jp, out):
    """**레트일 바이트가 그대로 남은** 레코드를 잡는다.

    폰트를 한글로 갈아 끼웠으므로 번역 안 된 레코드는 화면에서 일본어가 아니라
    뜻 모를 한글로 나온다(제3차 분기 선택지 `早乙女研究所/光子力研究所` →
    '건걷걸근귿글'). 한글맵으로 읽는 미번역 검사로는 절대 안 걸린다.
    바이트가 원문과 같고 **원문맵으로 읽으면 일본어**면 미번역이다.
    """
    import re
    JPRE = re.compile(r"[぀-ヿ一-鿿]")
    jt = {i: ch for i, ch in A.JP.items() if ch}   # 레트일(일본어) 글리프표
    try:
        from analyze_sce_relocation import parse_scenarios  # noqa: F401
    except Exception:
        pass
    bad = 0
    for (s, e, *_r), (sj, ej) in zip(recs_ko, recs_jp):
        if ko[s:e] != jp[sj:ej]:
            continue
        try:
            txt = A.decode(jp, sj, ej, jt)
        except Exception:
            continue
        letters = [c for c in txt if not c.isspace()]
        if len(letters) >= 3 and len(JPRE.findall(txt)) / len(letters) >= 0.5:
            bad += 1
    if bad:
        out.append((f"{name} 원문잔재", len(recs_ko), bad, 0, 0))


def check_pointer_ordinals(name, ko, jp, out):
    """대사 포인터가 **레트일과 같은 순번의 레코드**를 가리키는지.

    한글은 원문보다 길어 레코드가 통째로 밀린다. 변위를 안 고치면 그 대사만
    레코드 중간을 가리켜 화자가 사라지고 문장 중간부터 나온다. 재조준을 놓친
    옵코드가 있으면(2026-08-12 의 `B9 03`, 세 게임 367곳) 여기서 잡힌다.

    '레코드 시작을 가리킨다'만 보면 안 된다 — 레코드가 촘촘해서 어긋난 변위도
    우연히 어떤 레코드의 시작에 맞는다. **순번**을 맞춰 봐야 한다.
    """
    import struct
    sys.path.append(str(_P.REPO / "tools"))
    from analyze_sce_relocation import parse_scenarios, iter_pointer_sites
    bad = tot = 0
    for x, y in zip(parse_scenarios(jp), parse_scenarios(ko)):
        if len(x.records) != len(y.records):
            continue
        oj = {r.start: i for i, r in enumerate(x.records)}
        ok_ = {r.start: i for i, r in enumerate(y.records)}
        # 풀 앞 스크립트는 번역으로 안 바뀌지만 **블록 시작 자체가 밀린다**.
        # 절대 오프셋이 아니라 블록 기준 상대 위치로 짝지어야 한다.
        prepool_ok = ((x.pool_start - x.block_start) == (y.pool_start - y.block_start))
        for lo, hi in ((x.block_start, x.pool_start), (x.pool_start, x.record_data_end)):
            for off_j, opnd_j, op in iter_pointer_sites(jp, lo, hi):
                tj = opnd_j + struct.unpack_from("<h", jp, opnd_j)[0]
                if tj not in oj:
                    continue
                if off_j < x.pool_start:
                    if not prepool_ok:
                        continue
                    off_k = y.block_start + (off_j - x.block_start)
                else:
                    host = next((i for i, r in enumerate(x.records)
                                 if r.start <= off_j < r.end), None)
                    if host is None:
                        continue
                    off_k = y.records[host].start + (off_j - x.records[host].start)
                if off_k + 4 > len(ko) or ko[off_k] != op:
                    continue          # 번역으로 밀린 자리 — 대조 대상이 아니다
                opnd_k = off_k + (opnd_j - off_j)
                tk = opnd_k + struct.unpack_from("<h", ko, opnd_k)[0]
                tot += 1
                if ok_.get(tk) != oj[tj]:
                    bad += 1
    # 아직 못 잡은 잔여. 두 곳 다 **레코드 0(이벤트 스크립트) 안의 촘촘한 포인터
    # 표**인데, 변위를 고치면 그 2바이트가 레코드 훑기에서 0xFF 로 읽혀 경계가
    # 움직이고, 다시 계산하면 또 어긋나 되풀이해도 수렴하지 않는다. 지금은
    # 레트일 변위 그대로 둔다(대사가 밀리지만 진행에는 지장 없음).
    # 여기 적힌 수보다 늘면 **새로 생긴 회귀**이므로 빌드를 세운다.
    known = {"제2차": 1, "EX": 4}.get(name, 0)
    if bad > known:
        out.append((f"{name} 대사포인터", tot, 0, bad - known, 0))
    elif bad:
        print(f"  ({name} 대사포인터: 알려진 잔여 {bad}건 — 레코드 0 포인터 표)")


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
    print(f"  레이아웃 폭 자체검사: 상자 32 중 {check_wrap_width()} 칸 채움 (>18 이어야 정상)")

    rows = []
    for game, sce, bmess, dead, extras in GAMES:
        tbl = A.ko_table(extras)
        d = A.read_iso(img, sce)
        j = (_P.EXTRACTED / sce).read_bytes()
        ko_recs, jp_recs = _paired(d, j, tbl)
        check_pages(f"{game} 대사", d, j, ko_recs, jp_recs, tbl, SCE_BOX_ADVANCE, rows)
        check_objective_block(game, d, j, tbl, rows)
        check_retail_leftovers(f"{game} 대사", d, j, ko_recs, jp_recs, rows)
        check_pointer_ordinals(game, d, j, rows)
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
