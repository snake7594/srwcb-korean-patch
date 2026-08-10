"""In-place Korean injection for the game-quit / power-off message pool.

Records are located by their original Japanese speaker anchor (offset-independent,
so the same routine works on CB SECOND.WAR/THIRD.WAR and the standalone SLPS exes).
Each record is FF-terminated at a fixed offset referenced by the title-screen VM
(`b3 <u16 field-relative>` pointers), so writing Korean of length <= the original
record span keeps every pointer valid without relocation.

Translations come from quit_ko.json (built + decode-back verified separately).
"""
import json, os, sys
from pathlib import Path

# --- 이식용 부트스트랩 (자동 삽입) ---
_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
if _d not in sys.path:
    sys.path.insert(0, _d)
import srwcb_paths as _P
# ------------------------------------
sys.path.insert(0, str(_P.TOOLS))
from second_translation_codec import (load_safe_glyph_map, add_extra_glyph_mapping,
                                       normalise_for_font)

MAP = json.loads(_P.FONT_MAPPING.read_text(encoding="utf-8"))
C2I = {}
for r in MAP["rows"]:
    ch = r.get("character")
    if ch and ch not in C2I:
        C2I[ch] = r["glyph_index"]
CTRL = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}
KO = json.loads((_P.TRANSLATION / "quit_messages_ko.json").read_text(encoding="utf-8"))

SECOND_EXTRAS = ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응']
THIRD_EXTRAS  = ['×', '…', '↑', '→', '↓', '○', '릭', '응']

# ordered (anchor/main-key, [continuation keys]) per game
SECOND_ORDER = [
    ("マサキ「おっ", []),
    ("シュウ「おや", []),
    ("ビアン「ほお", []),
    ("甲児「さあて", ["甲児p2"]),
    ("竜馬「そろそろ", ["竜馬p2"]),
]
THIRD_ORDER = [
    ("アギ-ハ「ま", []),
    ("ヴィガジ「これが", []),
    ("ヴィガジ「いいか", []),
    ("ウェンドロ「あいつ", []),
    ("ウェンドロ「あれ", []),
    ("ウェンドロ「でも", []),
    ("甲児「さあて", ["甲児p2"]),
    ("竜馬「そろそろ", ["竜馬p2"]),
    ("豹馬「おっと", ["豹馬p2"]),
    ("万丈「ごくろ", ["万丈p2", "万丈p3"]),
]

def enc_jp(s):
    o = bytearray()
    for ch in s:
        i = C2I[ch]
        o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
    return bytes(o)

def make_enc_ko(extras):
    gm = add_extra_glyph_mapping(load_safe_glyph_map(), extras)
    def enc(s):
        o = bytearray()
        for k, part in enumerate(s.split("[F6]")):
            if k > 0:
                o.append(0xF6)
            for ch in normalise_for_font(part)[0]:
                i = gm[ch]
                o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
        o.append(0xFF)
        return bytes(o)
    return enc

def rec_end(buf, start):
    p, N = start, len(buf)
    while p < N:
        b = buf[p]
        if b == 0xFF:
            return p + 1
        if b < 0xEB:
            p += 1
        elif b <= 0xF5:
            p += 2
        else:
            p += 1 + CTRL.get(b, 0)
    raise ValueError("no FF terminator from 0x%x" % start)

def inject(buf, game, verbose=True, retail=None):
    """종료 메시지를 넣는다.

    `retail` 을 주면 **앵커와 레코드 경계를 원문에서 찾는다**. 중간 산출물이
    이미 한 번 주입된 상태여도 번역을 고치면 그대로 다시 반영된다(예전에는
    앵커(일본어)가 안 보이면 '이미 한글'로 건너뛰어, 고친 번역이 제3차에
    영원히 안 들어갔다 — 2026-08-10).
    """
    buf = bytearray(buf)
    order = SECOND_ORDER if game == "second" else THIRD_ORDER
    texts = KO[game]
    enc_ko = make_enc_ko(SECOND_EXTRAS if game == "second" else THIRD_EXTRAS)
    report = []
    written = 0
    ref = bytearray(retail) if retail is not None else buf
    for anchor, conts in order:
        pat = enc_jp(anchor)
        hits = []
        at = ref.find(pat)
        while at >= 0:
            hits.append(at)
            at = ref.find(pat, at + 1)
        if len(hits) != 1:
            report.append((anchor, "FOUND=%d" % len(hits), 0, 0, False))
            continue
        start = hits[0]
        bounds = []
        s = start
        end = rec_end(ref, s)
        bounds.append((s, end, texts[anchor]))
        for ck in conts:
            s = end
            end = rec_end(ref, s)
            bounds.append((s, end, texts[ck]))
        plans, ok_all = [], True
        for s, e, txt in bounds:
            data = enc_ko(txt)
            budget = e - s
            ok = len(data) <= budget
            # 한국어가 짧아 남는 꼬리에 원문이 그대로 있으면, 렌더러가 종료자
            # 다음으로 넘어가면서 그 일본어를 다음 쪽으로 보여 준다(2026-08-10
            # 제보 #4). 빈칸으로 채워 레코드를 정확히 메운다.
            if ok and len(data) < budget:
                data = data[:-1] + bytes(budget - len(data)) + bytes((0xFF,))
            ok_all &= ok
            plans.append((s, e, data, budget, ok))
        for i, (s, e, data, budget, ok) in enumerate(plans):
            tag = anchor if i == 0 else anchor + "#p%d" % i
            report.append((tag, "0x%05x" % s, len(data), budget, ok))
        if ok_all:
            for s, e, data, budget, ok in plans:
                buf[s:s + len(data)] = data
                written += 1
    if verbose:
        for tag, where, ln, bud, ok in report:
            print(f"  {tag:26s} {where:>9} ko={ln:>3} bud={bud:>3} {'OK' if ok else 'MISS'}")
        print(f"  records written: {written}")
    return bytes(buf), report
