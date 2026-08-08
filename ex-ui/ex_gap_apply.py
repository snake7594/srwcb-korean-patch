# -*- coding: utf-8 -*-
"""EX E_SCE 원장 누락 레코드 보충 — 레코드별 직접 치환.

패턴 매칭(apply_phrases)은 접두부/부분 일치로 이중 치환이 나기 쉬워 쓰지 않는다.
누락 레코드의 오프셋을 모두 알고 있으므로 레코드마다 토큰을 분해해
  [앞 컨트롤·스크립트 바이트][텍스트 구간][뒤 컨트롤][FF]
에서 **텍스트 구간만** 한국어로 바꾸고 나머지 바이트는 그대로 둔다.

  * 앞에 스크립트 바이트가 없는 순수 대사 -> LayoutState로 인코딩
      (렌더러의 자동 줄바꿈 버그를 피하려면 반드시 사전 줄바꿈이 필요)
  * 앞에 스크립트 바이트가 있는 목표/조건문 -> 접두부 보존 + 평문 인코딩(짧아서 줄바꿈 불필요)
"""
import json, re, sys, os

# --- 이식용 부트스트랩 (자동 삽입) ---
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
import srwcb_paths as _P
for _sub in ("tools", "ex-ui"):
    _p = os.path.join(_d, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.append(_p)
# ------------------------------------

DATA = _P.REPO / "ex-ui" / "data"


def _line_advs(rec):
    """레코드 바이트에서 [F6]/[F7] 로 나뉜 줄마다 렌더러 advance 를 센다.
    advance 는 바이트 길이가 아니라 글리프 인덱스로 정해진다(0x101 미만은 항상 1)."""
    out = [0]; ph = 0; i = 0
    while i < len(rec):
        x = rec[i]
        if x == 0xFF: break
        if x < 0xEB: idx = x; i += 1
        elif x < 0xF6: idx = ((x - 0xEB) << 8) | rec[i + 1]; i += 2
        else:
            if x in (0xF6, 0xF7): out.append(0); ph = 0
            i += 1; continue
        if idx < 0x101: out[-1] += 1
        else: out[-1] += 1 + ph; ph ^= 1
    return out


def _load_gap_translations():
    """갭 레코드의 '일본어 원문 -> 한국어' 대응표(저장소 자산).

    값에 들어 있는 `<f6>` 는 **줄바꿈 위치를 못 박아 둔 것**이다. 렌더러의
    자동 줄바꿈은 2바이트 글리프 선두를 잃는 버그가 있어서, 이 파이프라인은
    줄바꿈을 미리 넣어 둔다. 그래서 번역을 고칠 때는 한 줄이 렌더러 advance
    18(전각 9자)을 넘지 않게 `<f6>` 를 다시 배치해야 한다.
    """
    p = DATA / "ex_gap_translations.json"
    assert p.exists(), f"갭 번역 파일이 없습니다: {p}"
    return {k: v for k, v in json.loads(p.read_text(encoding="utf-8")).items() if v}


def build_ex_supplement(glyph_map, src_sce, idx2ch):
    from second_translation_codec import normalise_for_font, LayoutState, record_geometry
    from sce_gap_supplement import make_encoder, _tokens as _tok, _rec_end
    enc_ko = make_encoder(glyph_map, idx2ch)
    tr = _load_gap_translations()
    real = json.loads((DATA / "sce_gap_real.json").read_text(encoding="utf-8"))
    th_by_jp = json.loads((DATA / "third_dial_by_jp.json").read_text(encoding="utf-8"))

    def is_text_glyph(rec, off, size):
        ch = (idx2ch.get(rec[off], "") if size == 1
              else idx2ch.get(((rec[off] - 0xEB) << 8) | rec[off + 1], ""))
        return len(ch) == 1
    MAXLINE = 18
    def adv(s):
        ph = 0; a = 0
        for ch in normalise_for_font(s)[0]:
            i = glyph_map.get(ch)
            if i is None: a += 1
            elif i < 0x101: a += 1
            else: a += 1 + ph; ph ^= 1
        return a

    # ── 선택지/메뉴 레코드 폭 교정 ──────────────────────────────────────────
    # 앞에 스크립트 바이트가 있는 레코드는 평문 인코딩이라 자동 줄바꿈이 없다.
    # 이들 중 선택지·챕터 메뉴는 창 폭이 레트일 텍스트 기준으로 잡히므로
    # 줄마다 레트일 advance 이하로 줄여야 한다(넘치면 글자가 겹쳐 깨진다).
    KO_OVERRIDE = {
        # 선택지: 폭 18 안에 들어와야 겹치지 않는다 (17 / 8)
        "マサキ「ほう,まだそんな口がきけるのか‥‥」<f6>無言ではり倒す":
            "마사키「입이 살았군‥‥」<f6>말없이 강타",
        # 챕터 선택 메뉴: 12 / 11 / 12
        "マサキの章(やさしい)<f6>リュ-ネの章(ふつう)<f6>シュウの章(むずかしい)":
            "마사키편(쉬움)<f6>류네편(보통)<f6>슈우편(어려움)",
        # 선택지: 얀롱(‥‥그렇군)(13) / (크‥‥쓸데없는 참견이다)(18)
        "ヤンロン(‥‥そうだな)<f6>ヤンロン(ぐ‥‥よけいなお世話だ)":
            "얀롱(‥‥그렇군)<f6>얀롱(큭‥‥쓸데없는 참견)",
        # 튜토리얼 성공 메시지(10)
        "成功!<f6>つぎいってみよう!!": "성공!<f6>다음 가자!!",
    }
    sup = {}
    stat = {"layout": 0, "prefix_kept": 0, "reused_third": 0, "skipped": 0, "wide": []}
    # 반복되는 승리/패배 조건문 (번역 배치에 넣지 않았던 것들). 제2차·제3차 표기와 동일.
    CONDITIONS = {
        "味方の全滅": "아군 전멸", "敵の全滅": "적 전멸", "敵のせん滅": "적 섬멸",
        "敵の全滅<f6>敵のせん滅": "적 전멸<f6>적 섬멸",
    }
    for g in real:
        jp = g["jp"]; ko = KO_OVERRIDE.get(jp) or tr.get(jp) or CONDITIONS.get(jp)
        if not ko: stat["skipped"] += 1; continue
        off = g["off"]; end = _rec_end(src_sce, off)
        rec = bytes(src_sce[off:end])
        toks = _tok(rec, 0, len(rec))
        # 텍스트 구간(첫 텍스트 글리프 ~ 마지막 텍스트 글리프/F6) 찾기
        first = last = None
        for i, (o, size, kind, _x) in enumerate(toks):
            textish = (kind == 'g' and is_text_glyph(rec, o, size)) or (kind == 'c' and rec[o] == 0xF6)
            if textish:
                if first is None and kind == 'g': first = i
                if first is not None: last = i
        if first is None: stat["skipped"] += 1; continue
        while last is not None and toks[last][2] == 'c':   # 끝의 F6은 텍스트 구간에서 제외
            last -= 1
        head = rec[:toks[first][0]]
        tail = rec[toks[last][0] + toks[last][1]:]
        # 접두부에 스크립트 바이트가 있으면 세그먼트를 붙여서 평문 인코딩(접두부 보존)
        segs = None if jp in KO_OVERRIDE else th_by_jp.get(jp)
        if segs:
            stat["reused_third"] += 1
        else:
            segs = []
            for i, s in enumerate(ko.split("<f6>")):
                if i: segs.append("<f6>")
                segs.append(s)
        if head:
            body = b""
            for s in segs:
                if s == "<f6>": body += b"\xF6"
                elif s == "<f7>": body += b"\xF7"
                else: body += enc_ko(s)
            sup[off] = head + body + tail
            stat["prefix_kept"] += 1
            # 평문 경로에는 자동 줄바꿈이 없다 → 줄마다 레트일 폭을 넘지 않는지 확인.
            for _i, (_r, _n) in enumerate(zip(_line_advs(rec), _line_advs(sup[off]))):
                if _n > _r: stat["wide"].append((off, f"line{_i}", _n, _r))
        else:
            _w, _l = record_geometry(rec)
            # 원문 대사에는 F7(쪽 나눔)이 한 개도 없다. 3줄 안에 들어가면 F6 만
            # 쓰고, 도저히 안 되는 긴 대사만 쪽을 나눈다.
            enc = None
            for _pb in (False, True):
                st = LayoutState(glyph_map, max_advance=max(_w, 32), max_lines=3,
                                 allow_page_break=_pb)
                for s in segs:
                    if s == "<f6>":
                        st.pending_spaces = 0; st._new_line_or_page()
                    elif s == "<f7>":
                        st.preserve_page_break(b"\xF7")
                    else:
                        st.emit_text(normalise_for_font(s)[0])
                enc, _m = st.finish()      # finish()가 FF까지 붙임
                if max(len(pg) for pg in _m["pages"]) <= 3:
                    break
            sup[off] = bytes(enc)
            stat["layout"] += 1
    return sup, stat
