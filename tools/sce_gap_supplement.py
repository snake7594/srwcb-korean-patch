# -*- coding: utf-8 -*-
"""Build supplemental 3_SCE replacements for the 159 ledger-missed records.

Returns {retail_record_offset: full_replacement_record_bytes} to merge into the
sce_replacements dict produced by make_replacements() (rebuild_second_sce
relocates records, so replacements may shrink/grow freely).

Two mechanisms:
  * DIAL records: full-record rebuild — control skeleton verified byte-exact
    against retail, text segments replaced with Korean.
  * Script/bytecode records: token-level phrase substitution — patterns are
    located by DECODING the retail record (never by re-encoding Japanese, so
    duplicate-glyph ambiguity cannot break matching); all surrounding bytecode
    stays byte-exact. The combo 敵の全滅[F6]敵のせん滅 collapses to single
    "적 섬멸" (SECOND.WAR precedent).
"""
import pickle, sys

CTRL = {0xF6:0,0xF7:0,0xF8:1,0xF9:1,0xFA:0,0xFB:2,0xFC:2,0xFD:2,0xFE:1}

def _rec_end(b, pos):
    p = pos
    while p < len(b):
        x = b[p]
        if x == 0xFF: return p + 1
        p += 1 if x < 0xEB else (2 if x <= 0xF5 else 1 + CTRL.get(x, 0))
    return p

def _tokens(b, s, e):
    """[(off, size, kind, char)] kind: 'g' glyph / 'c' control / 'end'"""
    out = []; p = s
    while p < e:
        x = b[p]
        if x == 0xFF: out.append((p, 1, 'end', '')); break
        if x < 0xEB: out.append((p, 1, 'g', None)); p += 1
        elif x <= 0xF5: out.append((p, 2, 'g', None)); p += 2
        else:
            n = 1 + CTRL.get(x, 0); out.append((p, n, 'c', None)); p += n
    return out

def apply_phrases(rec, pats, idx2ch):
    """token-level phrase substitution inside one record's bytes; returns new bytes
    (or None if unchanged). '\\x00' in a pattern char-string matches one F6 token."""
    rec = bytearray(rec)
    changed = True; any_change = False
    while changed:
        changed = False
        toks = _tokens(bytes(rec), 0, len(rec))
        chars = []
        for off, size, kind, _ in toks:
            if kind == 'g':
                ch = (idx2ch.get(rec[off], "") if size == 1
                      else idx2ch.get(((rec[off] - 0xEB) << 8) | rec[off + 1], ""))
                chars.append(ch if len(ch) == 1 else '\x02')
            elif kind == 'c' and rec[off] == 0xF6: chars.append('\x00')
            else: chars.append('\x01')
        s = "".join(chars)
        for jp, ko_bytes in pats:
            i = s.find(jp)
            if i < 0: continue
            b0 = toks[i][0]; last = toks[i + len(jp) - 1]
            rec[b0:last[0] + last[1]] = ko_bytes
            changed = True; any_change = True
            break
    return bytes(rec) if any_change else None

def build_supplement(glyph_map, src_sce, idx2ch, sp_dir):
    from second_translation_codec import normalise_for_font
    sys.path.insert(0, sp_dir)
    from sce_gap_translations import DIAL, PHRASES

    ch2low = {}
    for i, c in sorted(idx2ch.items()):
        if c and c not in ch2low: ch2low[c] = i

    def enc_ko(t):
        o = bytearray()
        for ch in normalise_for_font(t)[0]:
            i = glyph_map.get(ch)
            if i is None:
                i = ch2low.get(ch)
                assert i is not None and i < 0x101, f"unencodable char {ch!r} in {t!r}"
            o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
        return bytes(o)

    def glyph_char(b, off, size):
        if size == 1: return idx2ch.get(b[off], "")
        return idx2ch.get(((b[off] - 0xEB) << 8) | b[off + 1], "")

    sup = {}

    # ---------- 1) DIAL full-record rebuilds via the dialogue LayoutState ----------
    # Same path as the main pipeline: auto-wrap at MAX_LINE_ADVANCE=18 (the renderer's
    # buggy auto-wrap must never fire), <=3 lines/page with automatic F7 paging.
    # '<f6>' = explicit line break (choice rows / intended splits), '<f7>' = page break.
    from second_translation_codec import LayoutState
    for off, segs in DIAL.items():
        st = LayoutState(glyph_map)
        for s in segs:
            if s == "<f6>":
                st.pending_spaces = 0          # break itself is the separator
                st._new_line_or_page()
            elif s == "<f7>":
                st.preserve_page_break(b"\xF7")
            else:
                st.emit_text(normalise_for_font(s)[0])
        enc, _m = st.finish()          # finish() already appends the FF terminator
        sup[off] = bytes(enc)

    # ---------- 2) phrase substitution in script/'A records ----------
    pats = make_pats(enc_ko)
    cand = _phrase_candidates(src_sce, sp_dir)
    report = {}
    for start in cand:
        if start in sup:
            continue
        end = _rec_end(src_sce, start)
        new = apply_phrases(src_sce[start:end], pats, idx2ch)
        if new is not None:
            sup[start] = new
            report[start] = True
    return sup, {"phrase_records": len(report)}

def _phrase_candidates(src_sce, sp_dir):
    """구절 치환을 시도할 레코드 시작 목록.

    예전엔 작업 중 만든 pickle(`sce_gaps_retail.pkl`)에 담아 뒀는데, 그 파일이 없는
    사람은 빌드를 할 수 없었다. 후보를 넓게 잡아도 `apply_phrases` 가 아는 구절이
    없으면 None 을 돌려주므로, **모든 레코드 시작**을 후보로 주면 결과가 같다.
    """
    import os
    pkl = f"{sp_dir}/sce_gaps_retail.pkl"
    if os.path.exists(pkl):
        gaps = pickle.load(open(pkl, "rb"))
        return [g[2] for g in gaps["script"]] + [0x3b9d4, 0x642b4, 0x9553c]
    from analyze_sce_relocation import parse_scenarios
    out = []
    for s in parse_scenarios(bytes(src_sce)):
        out += [r.start for r in s.records]
    return out


def make_encoder(glyph_map, idx2ch):
    from second_translation_codec import normalise_for_font
    ch2low = {}
    for i, c in sorted(idx2ch.items()):
        if c and c not in ch2low: ch2low[c] = i
    def enc_ko(t):
        o = bytearray()
        for ch in normalise_for_font(t)[0]:
            i = glyph_map.get(ch)
            if i is None:
                i = ch2low.get(ch)
                assert i is not None and i < 0x101, f"unencodable char {ch!r} in {t!r}"
            o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
        return bytes(o)
    return enc_ko

def make_pats(enc_ko):
    """[(char_pattern, ko_bytes)] longest-first; '\\x00' = one F6 token."""
    sys_path_hack = None
    from sce_gap_translations import PHRASES
    pats = [("敵の全滅\x00敵のせん滅", enc_ko("적 섬멸"))]
    pats += [(jp, enc_ko(ko)) for jp, ko in PHRASES if jp != "敵の全滅敵のせん滅"]
    pats.sort(key=lambda x: -len(x[0]))
    return pats
