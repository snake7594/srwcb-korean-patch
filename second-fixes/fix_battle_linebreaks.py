# -*- coding: utf-8 -*-
"""제2차 전투 후 대사 밀림 수정 — 배틀/사망 대사의 잉여 줄바꿈(F6/F7) 제거.

근본원인(역분석 확정): 배틀 대사 렌더러는 시나리오와 다른 경로로, 대사를 폭 40
'한 줄'에 표시한다(F7 없으면 X≥40 래핑). 그런데 한글 코덱이 시나리오용 18유닛
pre-break 를 배틀·사망 대사에도 적용해, 원판 1줄짜리(≤29유닛)를 18에서 강제로
쪼갰다(F6 폭증). 이 잉여 개행이 대사를 밀어낸다.

원장(translation_parts)에 배틀·사망 대사의 개행이 하나도 없음을 확인했다 — 즉
배포본의 F6/F7 은 100% 코덱이 삽입한 pre-break 다. 따라서 참조되는(번역된)
레코드에서 F6/F7 컨트롤만 제거하고 텍스트를 당긴 뒤 뒤를 0으로 채우면 원판 1줄이
복원된다. 레코드 시작·포인터·표·파일 크기는 모두 그대로다(제자리 수정).

검증: 제거 후 모든 레코드가 40유닛 이내(배틀 박스)에 들어감을 확인했다.
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
if str(_P.TOOLS) not in _sys.path:
    _sys.path.insert(0, str(_P.TOOLS))
# ------------------------------------------------------------------
import sys, math, struct
from pathlib import Path

R = str(_P.WORK)
sys.path.insert(0, f"{R}/tools")
from analyze_second_message_archives import parse_bmess, parse_dead
from second_translation_codec import glyph_advance

ARG = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}


def _rewrap_one(out, start, end, W=40):
    """out[start:end) 의 잉여 F6/F7 을 걷어내고, 배틀 박스 폭 W 로 다시 줄을 나눈다.

    18유닛 pre-break 를 제거하면 대부분 대사가 1줄(≤W)로 복원된다. W 를 넘는
    소수 대사만 W 직전 글리프 경계에서 F6(하드 개행)로 나눈다. 배틀 렌더러의
    F6=개행(Y증가)와 정확히 맞는다. F8~FE 원판 컨트롤은 그대로 보존한다.
    결과 길이는 원본(18 pre-break, F6 다수) 이하라 제자리에 들어간다.
    """
    toks = []
    p = start
    while p < end:
        x = out[p]
        if x == 0xFF:
            break
        if x < 0xEB:
            toks.append((bytes([x]), x)); p += 1
        elif x < 0xF6:
            toks.append((bytes(out[p:p + 2]), ((x - 0xEB) << 8) | out[p + 1])); p += 2
        elif x in (0xF6, 0xF7):
            p += 1
        else:
            n = 1 + ARG.get(x, 0); toks.append((bytes(out[p:p + n]), None)); p += n
    res = bytearray(); adv = 0; ph = 0
    for b, gi in toks:
        if gi is None:
            res += b; continue
        step, nph = glyph_advance(gi, ph)
        if adv and adv + step > W:
            res.append(0xF6); adv = 0; ph = 0
            step, nph = glyph_advance(gi, ph)
        res += b; adv += step; ph = nph
    res.append(0xFF)
    L = len(res)
    if L > end - start:
        raise ValueError(f"레코드 {start:#x} 재줄바꿈 후 길이 초과 {L}>{end-start}")
    removed = (end - start) - L
    out[start:start + L] = res
    out[start + L:end] = b"\x00" * removed
    return removed


def _strip_one(out, start, end):
    """out[start:end) 에서 F6/F7 컨트롤 제거. [kept..][0xFF] 로 축약 후 0 패딩."""
    p = start
    kept = bytearray()
    while p < end:
        x = out[p]
        if x == 0xFF:
            break
        if x < 0xEB:
            kept.append(x); p += 1
        elif x < 0xF6:
            kept += bytes(out[p:p + 2]); p += 2
        elif x in (0xF6, 0xF7):
            p += 1                      # 잉여 개행 제거
        else:
            n = 1 + ARG.get(x, 0); kept += bytes(out[p:p + n]); p += n
    kept.append(0xFF)
    L = len(kept)
    if L > end - start:
        raise ValueError(f"레코드 {start:#x} 축약 실패 {L}>{end-start}")
    out[start:start + L] = kept
    out[start + L:end] = b"\x00" * (end - start - L)
    return (end - start) - L


def _max_line_adv(buf, start, end):
    """F6/F7 로 나뉜 줄의 최대 renderer advance (제거 후 검증용)."""
    p = start; adv = 0; ph = 0; mx = 0
    while p < end:
        x = buf[p]
        if x == 0xFF:
            break
        if x < 0xEB:
            s, ph = glyph_advance(x, ph); adv += s; p += 1
        elif x < 0xF6:
            gi = ((x - 0xEB) << 8) | buf[p + 1]; s, ph = glyph_advance(gi, ph); adv += s; p += 2
        elif x in (0xF6, 0xF7):
            mx = max(mx, adv); adv = 0; ph = 0; p += 1
        else:
            p += 1 + ARG.get(x, 0)
    return max(mx, adv)


def fix_bmess(data, box_width=40):
    ar = parse_bmess(data)
    out = bytearray(data)
    seen = set(); recs = 0; f6_removed = 0; over = []
    for blk in ar.blocks:
        for tgt in blk.text_references:          # 참조되는(번역된) 레코드만
            rec = blk.text_records[tgt]
            s = blk.file_start + 15 + rec.start
            if s in seen:
                continue                          # alias(같은 레코드 공유) 중복 방지
            seen.add(s)
            e = blk.file_start + 15 + rec.end
            f6_removed += _rewrap_one(out, s, e)
            recs += 1
            adv = _max_line_adv(out, s, e)
            if adv > box_width:
                over.append((blk.index, rec.start, adv))
    return bytes(out), recs, f6_removed, over


def fix_dead(data, box_width=40):
    da = parse_dead(data)
    out = bytearray(data)
    recs = 0; f6_removed = 0; over = []
    items = da.records.items() if isinstance(da.records, dict) else \
        [(r.start, r) for r in da.records]
    for st, rec in items:
        f6_removed += _rewrap_one(out, rec.start, rec.end)
        recs += 1
        adv = _max_line_adv(out, rec.start, rec.end)
        if adv > box_width:
            over.append((rec.start, adv))
    return bytes(out), recs, f6_removed, over


def _count_f6_in_refs(data, kind):
    """참조 레코드 내 F6/F7 컨트롤 개수(검증용)."""
    n = 0
    if kind == "bmess":
        ar = parse_bmess(data); seen = set()
        for blk in ar.blocks:
            for tgt in blk.text_references:
                rec = blk.text_records[tgt]
                s = blk.file_start + 15 + rec.start
                if s in seen: continue
                seen.add(s)
                e = blk.file_start + 15 + rec.end
                p = s
                while p < e:
                    x = data[p]
                    if x == 0xFF: break
                    if x < 0xEB: p += 1
                    elif x < 0xF6: p += 2
                    elif x in (0xF6, 0xF7): n += 1; p += 1
                    else: p += 1 + ARG.get(x, 0)
    return n


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--bmess"); ap.add_argument("--dead")
    ap.add_argument("--out-bmess"); ap.add_argument("--out-dead")
    a = ap.parse_args()
    if a.bmess:
        data = open(a.bmess, "rb").read()
        before = _count_f6_in_refs(data, "bmess")
        fixed, recs, rm, over = fix_bmess(data)
        after = _count_f6_in_refs(fixed, "bmess")
        assert len(fixed) == len(data), "BMESS2 크기 변동"
        print(f"BMESS2: 레코드 {recs}, F6/F7 제거 {rm}B, 참조내 F6/F7 {before}->{after}, "
              f"40초과 {len(over)}, 크기 {len(fixed)} 불변")
        if a.out_bmess and not over:
            open(a.out_bmess, "wb").write(fixed); print(f"  WROTE {a.out_bmess}")
    if a.dead:
        data = open(a.dead, "rb").read()
        fixed, recs, rm, over = fix_dead(data)
        assert len(fixed) == len(data), "2_DEAD 크기 변동"
        print(f"2_DEAD: 레코드 {recs}, F6/F7 제거 {rm}B, 40초과 {len(over)}, "
              f"크기 {len(fixed)} 불변")
        if a.out_dead and not over:
            open(a.out_dead, "wb").write(fixed); print(f"  WROTE {a.out_dead}")
