# -*- coding: utf-8 -*-
"""TR.WAR(트레이닝 모드) UI injection — inject_ex_ui.py의 TR 적응판.

TR.WAR 은 EX.WAR 과 같은 엔진이고 UI 테이블 본문은 헤더 4바이트만 빼면
바이트까지 동일하다(실측). 오프셋만 다르므로 EX 에서 폭 검증된 번역을
그대로 쓴다. 이 파일은 make_tr_injector.py 가 inject_ex_ui.py 에서
기계 변환한 것이다 — 직접 고치지 말고 원본을 고친 뒤 다시 생성할 것.

EX는 THIRD와 엔진/레코드 구조가 1:1이라 오프셋만 다르다(정찰 확인).
제2차·제3차에서 터진 실수를 그대로 봉쇄한다:
  * caves 금지 — 도너는 폰트 미사용 글리프 슬롯만 (v3 프리즈 원인)
  * ui_master는 extent-preserving in-place, 넘치는 것만 도너 (윈도우-워크 앵커 보존)
  * 레코드 통째 재토큰화 금지 — 교체 스팬 단위로만 (UI-VM 옵코드 F0-F5 겹침)
  * 스팬은 원본 renderer advance/phase 시그니처에 맞춤 (고정폭 셀 깨짐 방지)
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
for _sub in ("tools", "third-ui", "ex-ui", "tr-ui", "audit", "menu-align", "second-fixes"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import json, struct, sys, os, re, hashlib

ROOT = str(_P.WORK)
SP = str(_P.BUILD)
sys.path.insert(0, str(_P.TOOLS)); sys.path.insert(0, SP)

from second_translation_codec import (load_safe_glyph_map, add_extra_glyph_mapping,
                                      required_extra_characters, normalise_for_font)
from build_exe_hangul_font import EXE_LAYOUT as FONT_EXE_LAYOUT

SRC = f"{ROOT}/test_build/ex_full/font_extracted/TR.WAR"
war = bytearray(open(SRC, "rb").read()); N = len(war)
assert war[:8] == b"PS-X EXE" and N == 0x123800, "SRC가 TR.WAR pre-inject 빌드가 아닙니다!"
RETAIL = open(f"{ROOT}/extracted/TR.WAR", "rb").read()
# ★이중 주입 가드(계획서 B-14): 크기/매직만으로는 이미 주입된 파일을 걸러내지 못한다.
# 명령 메뉴(0x974b)는 주입 시 한글로 덮이므로, 여기가 레트일과 다르면 이미 주입된 것이다.
assert war[0x9747:0x9747 + 76] == RETAIL[0x9747:0x9747 + 76], (
    "SRC가 이미 주입된 TR.WAR입니다 — font_extracted/TR.WAR 을 다시 만드세요")

mpj = json.load(open(f"{ROOT}/research/srwcb_embedded_font_mapping_reviewed.json", encoding="utf-8"))
idx2ch = {r["glyph_index"]: (r.get("character") or "") for r in mpj["rows"]}
GLYPH_COUNT, GLYPH_BYTES = 2816, 32
STRUCT_GLYPHS = {0x3FF}          # EE FF = 투명 전각 스페이서 (우측정렬 패딩)

def s32(o): return struct.unpack_from("<i", war, o)[0]
# 제3차 주입기와 동일한 토큰 문법(검증됨). F7은 인자 0개이고, 0xFF는 토큰 경계에서
# 먼저 판정하므로 컨트롤 인자가 종결자를 삼키지 않는다. rec_end와 rebuild가 반드시
# 같은 문법을 써야 재구성 길이가 원본과 일치한다(앵커 오탐 방지).
CTRL_ARGS = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}
def tokens_of(buf, pos, limit=None):
    p = pos; lim = limit or len(buf)
    while p < lim:
        b = buf[p]
        if b == 0xFF: yield (p, 1, 'end'); return
        if b < 0xEB: yield (p, 1, 'g'); p += 1
        elif b <= 0xF5: yield (p, 2, 'g'); p += 2
        else: n = 1 + CTRL_ARGS.get(b, 0); yield (p, n, 'c'); p += n
def rec_end(buf, s):
    for st, n, k in tokens_of(buf, s):
        if k == 'end': return st + 1
    return s
def decode(buf, s):
    o = []; p = s
    while p < len(buf) and buf[p] != 0xFF:
        x = buf[p]
        if x < 0xEB: o.append(idx2ch.get(x, '')); p += 1
        else: o.append(idx2ch.get(((x - 0xEB) << 8) | buf[p + 1], '')); p += 2
    return "".join(o)
def enc_jp(t):
    o = bytearray()
    for ch in t:
        i = next((k for k, v in idx2ch.items() if v == ch), None)
        if i is None: return None
        o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
    return bytes(o)

# ---------------- 번역 사전 ----------------
jp2ko = {}
for tb in json.load(open(f"{_P.TRANSLATION}/second_ui_tables_overlay.json", encoding="utf-8"))["tables"]:
    for e in tb["entries"]:
        if e.get("source_text") and e.get("korean_text") and str(e["korean_text"]).strip():
            jp2ko[e["source_text"]] = e["korean_text"]
for tb in json.load(open(f"{_P.TRANSLATION}/second_ui_names_overlay.json", encoding="utf-8"))["tables"]:
    for r in tb["rows"]:
        if r.get("japanese") and r.get("korean") and str(r["korean"]).strip():
            jp2ko[r["japanese"]] = r["korean"]
for fn in ("third_ui_translations.json", "msgpool_translations.json", "msgpool_srw_gloss.json"):
    p = f"{SP}/{fn}"
    if os.path.exists(p):
        for k, v in json.load(open(p, encoding="utf-8")).items():
            if not k.startswith("_") and v: jp2ko.setdefault(k, v)
# EX 전용 테이블/UI 번역
for p in (f"{_P.REPO}/ex-ui/data/ex_table_translations.json", f"{_P.REPO}/ex-ui/data/ex_ui_translations.json"):
    if os.path.exists(p):
        for k, v in json.load(open(p, encoding="utf-8")).items():
            if not k.startswith("_") and v: jp2ko[k] = v
# 이름표 입력 탭은 일본어 유지(가나 입력 그리드)
for _k in ("ひらがな", "カタカナ"): jp2ko.pop(_k, None)

span_map = {}
for a in json.load(open(f"{_P.TRANSLATION}/second_ui_scripts_overlay.json", encoding="utf-8"))["assets"].values():
    for r in a["records"]:
        for rep in r.get("replacements", []):
            if rep.get("korean_text"):
                span_map[bytes.fromhex(rep["source_hex"].replace(" ", ""))] = rep["korean_text"]
for jp, ko in jp2ko.items():
    b = enc_jp(jp)
    if b and len(b) >= 2: span_map.setdefault(b, ko)
span_map = {k: v for k, v in span_map.items() if v not in ("히라가나", "가타카나")}
# 띄어쓰기 오버플로 제거 규칙(v0.9.4) 재사용
_dn = f"{_P.TRANSLATION}/despace_nospace.json"
if os.path.exists(_dn):
    _nm = json.load(open(_dn, encoding="utf-8"))
    _st = lambda s: s.replace(" ", "").replace("　", "")
    for _b, _v in list(span_map.items()):
        if (" " in _v or "　" in _v) and _st(_v) in _nm and _nm[_st(_v)] != _v: span_map[_b] = _nm[_st(_v)]
    for _j, _k in list(jp2ko.items()):
        if _st(_k) in _nm and _nm[_st(_k)] != _k: jp2ko[_j] = _nm[_st(_k)]
maxspan = max(len(b) for b in span_map)

# ---------------- 폰트 도너 (caves 금지) ----------------
ko_all = list(jp2ko.values()) + list(span_map.values())
# 잔여 레코드용 신규 문자열도 폰트 유지 대상(=도너에서 제외)에 포함시켜야 한다.
import tr_extra_records as _XR0
ko_all += ([_XR0._ko_to_rec(v) for v in _XR0.LOCAL_KO.values()]      # '<f6>' -> '[F6]'
           + list(_XR0.IN_PLACE_EXACT.values()) + list(_XR0.KANJI2KO.values()))
_ovp = f"{_P.REPO}/ex-ui/data/ex_translation_overlay.json"
if os.path.exists(_ovp):   # 대사 오버레이(없으면 UI 단독 드라이런)
    ko_all += [v for t in json.load(open(_ovp, encoding="utf-8"))["translations"].values()
               for v in t["ko_parts"].values() if v]
else:
    print("[드라이런] 대사 오버레이 없음 — UI/테이블만 검증")
_STRIP = re.compile(r"\[F[6-9A-Ea-e]\]|\x01")   # \x01 = 전각 스페이서 센티널(글리프 아님)
ko_all = [_STRIP.sub("", x) for x in ko_all]
PINNED = ['×', '…', '↑', '→', '↓', '□', '△', '○', '릭', '응']
base = load_safe_glyph_map()
need = required_extra_characters([normalise_for_font(x)[0] for x in ko_all], base)
EXTRAS = PINNED + sorted(c for c in need if c not in PINNED)
assert EXTRAS[:len(PINNED)] == PINNED, "extras 순서 흔들림 — 기존 게임 깨짐 위험"
# 폰트는 build_ex_full.py 가 굽는다. 주입기 EXTRAS 가 폰트와 다르면 UI 글자가
# 존재하지 않는 글리프를 가리켜 전부 깨진다 → 반드시 일치해야 한다.
_fe = f"{_P.REPO}/ex-ui/data/ex_final_extras.json"
if os.path.exists(_fe):
    _want = json.load(open(_fe, encoding="utf-8"))["extras"]
    assert EXTRAS == _want, (f"EXTRAS 불일치!\n  폰트: {_want}\n  주입: {EXTRAS}\n"
                             "  → build_ex_full.py 를 먼저 다시 실행하거나 신규 문자를 없애세요")
gm = add_extra_glyph_mapping(base, EXTRAS)
print(f"EXTRAS {len(EXTRAS)} (고정 {len(PINNED)} + 신규 {len(EXTRAS)-len(PINNED)})")

_CTRL = re.compile(r"\[F([6-9A-Ea-e])\]")
# '\x01' = 0x3FF(EE FF) 투명 전각 스페이서. UI VM 은 이걸로 한 줄을 여러 칸(윈도)으로
# 나눈다. 스팬을 통째로 바꿀 때 이 바이트를 잃으면 칸이 하나로 합쳐져 텍스트가
# 칸 밖으로 밀리고 겹쳐 보인다(챕터 선택 화면 깨짐의 원인).
SPACER = "\x01"
def _enc_chars(t):
    o = bytearray()
    for j, part in enumerate(t.split(SPACER)):
        if j: o += b"\xEE\xFF"
        for ch in normalise_for_font(part)[0]:
            i = gm[ch]; o += bytes([i]) if i < 0xEB else bytes(((i >> 8) + 0xEB, i & 0xFF))
    return o
def enc_ko(s):
    o = bytearray(); pos = 0
    for m in _CTRL.finditer(s):
        o += _enc_chars(s[pos:m.start()])
        o.append(int("F" + m.group(1), 16)); pos = m.end()
    o += _enc_chars(s[pos:])
    return bytes(o)

used = set(STRUCT_GLYPHS)
for s in ko_all:
    for ch in normalise_for_font(s)[0]:
        i = gm.get(ch)
        if i is not None: used.add(i)
keep = set(range(0x000, 0x101)) | used
keep |= {i for i, c in idx2ch.items() if c in "○×△□◎☆★↑↓→←"}
font_off = next(v for k, v in FONT_EXE_LAYOUT.items() if str(k).replace("\\", "/") == "TR.WAR")
free = [i for i in range(0x101, GLYPH_COUNT) if i not in keep]
runs = []; st = pv = None
for i in free:
    if st is None: st = pv = i; continue
    if i == pv + 1: pv = i
    else: runs.append([font_off + st * GLYPH_BYTES, font_off + (pv + 1) * GLYPH_BYTES]); st = pv = i
if st is not None: runs.append([font_off + st * GLYPH_BYTES, font_off + (pv + 1) * GLYPH_BYTES])
ARENA = runs; arena_total = sum(b - a for a, b in ARENA); arena_used = 0
print(f"도너(폰트 전용) 블록 {len(ARENA)}개 / {arena_total} 바이트")
def arena_alloc(n):
    global arena_used
    best = None
    for bi, blk in enumerate(ARENA):
        room = blk[1] - blk[0]
        if room >= n and (best is None or room < ARENA[best][1] - ARENA[best][0]): best = bi
    if best is None: raise SystemExit(f"도너 부족: {n} 필요 (최대 {max(b-a for a,b in ARENA)})")
    off = ARENA[best][0]; ARENA[best][0] += n; arena_used += n; return off

# ---------------- 스팬 폭 보존 ----------------
HIGH_BLANK = b"\xEE\xFF"
_JPDEC = dict(idx2ch); _JPDEC[0x000] = " "
def _sig(idxs):
    ph = 0; adv = 0
    for i in idxs:
        if i < 0x101: adv += 1
        else: adv += 1 + ph; ph ^= 1
    return adv, ph
def _idxs_of(b):
    out = []; p = 0
    while p < len(b):
        x = b[p]
        if x < 0xEB: out.append(x); p += 1
        else: out.append(((x - 0xEB) << 8) | b[p + 1]); p += 2
    return out
align_fail = []
try:
    from third_align_overrides import ALIGN_OVERRIDES as _TH_OVR
except Exception:
    _TH_OVR = {}
_EX_OVR = {}
for _eo in (f"{_P.REPO}/ex-ui/data/ex_align_overrides.json", f"{_P.REPO}/tr-ui/tr_align_overrides.json"):
    if os.path.exists(_eo):
        for _k, _v in json.load(open(_eo, encoding="utf-8")).items():
            _EX_OVR.setdefault(_k, []).extend(_v if isinstance(_v, list) else [_v])
def _cands(jp, ko):
    """폭이 모자랄 때 시도 순서: 원문 -> 띄어쓰기 제거(v0.9.4 교훈) -> 수동 오버라이드."""
    out = [ko]
    d = ko.replace(" ", "").replace("　", "")
    if d and d != ko: out.append(d)
    for c in _EX_OVR.get(jp, []) or []: out.append(c)
    for c in _TH_OVR.get(jp, []) or []: out.append(c)
    return out
def fit_span(src_bytes, ko_text, tag, strict):
    target = _sig(_idxs_of(src_bytes))
    jp = "".join(_JPDEC.get(i, '·') for i in _idxs_of(src_bytes))
    def attempt(b):
        adv, ph = _sig(_idxs_of(b))
        if ph != target[1] and adv + 1 + ph <= target[0]:
            b = b + HIGH_BLANK; adv += 1 + ph; ph ^= 1
        if ph == target[1] and adv <= target[0]: return b + b"\x00" * (target[0] - adv)
        return None
    for cand in _cands(jp, ko_text):
        r = attempt(enc_ko(cand))
        if r is not None: return r
    if strict: align_fail.append((tag, jp, ko_text, _sig(_idxs_of(enc_ko(ko_text))), target))
    return enc_ko(ko_text)

def tokens(buf, start):
    return [(s, n, k) for s, n, k in tokens_of(buf, start) if k != 'end']
def rebuild_record(start, tag="?", allow_single=False):
    end = rec_end(war, start); out = bytearray(); p = start; hit = 0
    idx = {t[0]: t for t in tokens(war, start)}
    minL = 2 if allow_single else 3
    while p < end - 1:
        t = idx.get(p)
        if t is None: out.append(war[p]); p += 1; continue
        s_, n_, k_ = t
        if k_ == 'g':
            best = None
            for L in range(min(maxspan, end - 1 - p), minL - 1, -1):
                c = bytes(war[p:p + L])
                if c in span_map: best = (L, span_map[c]); break
            if best:
                src = bytes(war[p:p + best[0]]); nb = war[p + best[0]]
                strict = (nb == 0xFC) or (nb < 0xEB) or (0xEB <= nb <= 0xEF)
                out += fit_span(src, best[1], tag, strict)
                p += best[0]; hit += 1; continue
        out += war[s_:s_ + n_]; p = s_ + n_
    out.append(0xFF); return bytes(out), hit

manifest = []
def repack(name, entries, pool_lo, pool_hi, pf):
    newpos = {}; cur = pool_lo; ov = 0
    for t in sorted(entries):
        b = entries[t]
        newpos[t] = cur if cur + len(b) <= pool_hi else None
        if newpos[t] is not None: cur += len(b)
    for t in sorted([t for t in entries if newpos[t] is None], key=lambda t: -len(entries[t])):
        newpos[t] = arena_alloc(len(entries[t])); ov += len(entries[t])
    for t, b in entries.items(): war[newpos[t]:newpos[t] + len(b)] = b
    if cur < pool_hi: war[cur:pool_hi] = b"\x00" * (pool_hi - cur)
    for f, t in pf:
        if t in newpos: struct.pack_into("<i", war, f, newpos[t] - f)
    manifest.append(dict(asset=name, records=len(entries), pool_used=cur - pool_lo, pool=pool_hi - pool_lo, arena=ov))
    print(f"  {name:20s} recs={len(entries):>4} pool {cur-pool_lo:>6}/{pool_hi-pool_lo:<6} donor+{ov}")

# ---------------- 테이블 (EX 오프셋) ----------------
# TR 오프셋 (EX 대비 앞쪽 -8 / pilot·unit 계열 -0x24). verify_tr_offsets 가 검증한다.
TABLES = [("terrain_names", 0xbcac, 144, 0xc17c), ("spirit_commands", 0xc17c, 94, 0xc848),
          ("enhancement_parts", 0xc848, 64, 0xcbc4), ("weapon_names", 0xcbc4, 1344, 0xf250),
          ("pilot_skills", 0xf250, 52, 0xf508), ("unit_abilities", 0xf508, 22, 0xf614),
          ("scenario_titles", 0xf614, 192, 0xfbfc), ("pilot_short_names", 0x107768, 400, 0x108198),
          ("pilot_full_names", 0x108198, 400, 0x108f40), ("unit_names", 0x108f40, 448, 0x109fdc)]
SPIRIT_DESC_MAX = 34
_SDS = {}
try:   # 제3차에서 폭 검증된 정신기 설명 축약형 (정신기는 게임 간 동일)
    from third_align_overrides import SPIRIT_DESC_SHORT as _TH_SDS
    _SDS.update(_TH_SDS)
except Exception:
    pass
_sp = f"{_P.BUILD}/ex/ex_spirit_desc_short.json"
if os.path.exists(_sp): _SDS.update(json.load(open(_sp, encoding="utf-8")))
for name, ptr, cnt, bound in TABLES:
    pool_lo = ptr + 4 + 4 * cnt; recs = {}; pf = []
    for k in range(cnt):
        f = ptr + 4 + 4 * k; t = f + s32(f); pf.append((f, t))
        if not (pool_lo <= t and rec_end(war, t) <= bound) or t in recs: continue
        jp = decode(war, t); ko = jp2ko.get(jp)
        if name == "spirit_commands" and ko:
            adv, _ = _sig(_idxs_of(enc_ko(ko)))
            if adv > SPIRIT_DESC_MAX:
                ko = _SDS.get(jp, ko)
                adv, _ = _sig(_idxs_of(enc_ko(ko)))
                if adv > SPIRIT_DESC_MAX: align_fail.append(("spirit_desc", jp, ko, (adv, 0), (SPIRIT_DESC_MAX, 0)))
        recs[t] = (enc_ko(ko) + b"\xFF") if ko else bytes(war[t:rec_end(war, t)])
    repack(name, recs, pool_lo, min(max(rec_end(war, t) for t in recs), bound), pf)

# ---------------- ui_master (0x188C4, 107) extent-preserving ----------------
# 레코드별 단일 한자 리터럴 치환표(1한자=1한글, 바이트 길이 동일).
# 생년월일 피커/시나리오 번호/혈액형/기체 카운터 등 span_map이 못 잡는 1자 라벨.
# 모든 ui_master 레코드에 적용되는 리터럴 치환(길이 동일 + 토큰 경계에서만).
# 'はい'는 2바이트(가나×2)라 span_map minL=3에 안 걸리는데 선택지에 자주 나온다.
GLOBAL_LITERAL = {"はい": "예"}
def _literal_swap(rec, table):
    """토큰을 걸으며 '글리프 토큰 구간'에만 리터럴 치환. 컨트롤 인자는 절대 건드리지 않는다."""
    pairs = []
    for jp, ko in table.items():
        sb = enc_jp(jp); kb = enc_ko(ko)
        if sb and len(sb) == len(kb): pairs.append((sb, kb))
    if not pairs: return rec
    out = bytearray(); p = 0; n = len(rec)
    while p < n:
        x = rec[p]
        if x == 0xFF: out += rec[p:]; break
        if x >= 0xF6:                      # 컨트롤: 인자까지 그대로
            ln = 1 + CTRL_ARGS.get(x, 0); out += rec[p:p + ln]; p += ln; continue
        # 글리프 런 수집
        s = p
        while p < n and rec[p] != 0xFF and rec[p] < 0xF6:
            p += 1 if rec[p] < 0xEB else 2
        run = bytes(rec[s:p])
        for sb, kb in pairs: run = run.replace(sb, kb)
        out += run
    return bytes(out)

SPECIAL_SPANS = {
    15: {"機": "기"},
    31: {"第": "제"},
    41: {"月": "월", "日": "일", "型": "형"},
    42: {"月": "월", "日": "일", "型": "형"},
    43: {"月": "월", "日": "일", "型": "형"},
    45: {"月": "월", "日": "일"},
    46: {"月": "월", "日": "일"},
    71: {"第": "제"},
    93: {"第": "제", "話": "화"},
}
MH, MC = 0x188BC, 107

# ---- 줄 전체 스팬 오버라이드: 실제 레코드 바이트를 키로 등록 ----
# 같은 문자라도 게임이 중복 글리프 인덱스를 쓸 수 있어 enc_jp(텍스트)로 만든 바이트가
# 레코드와 일치하지 않는다(챕터 선택 줄이 그랬음). 그래서 레코드에서 스팬 바이트를 직접 뽑는다.
# 부분 문자열로 등록하면 나머지가 일본어로 남아 깨져 렌더되므로 반드시 '줄 전체'로.
# 챕터 선택 화면. 한 줄이 EE FF(0x3FF) 전각 스페이서로 [장 이름][난도] 두 칸으로
# 나뉘므로 반드시 SPACER 를 넣어야 한다(빠지면 두 칸이 합쳐져 글자가 겹친다).
# 각 칸의 advance 는 레트일 칸(마사키の章=5 / リュ-ネの章=6 / シュウの章=5) 이하로.
# 이름 칸은 레트일 칸 폭(마사키の章=5 / リュ-ネの章=6 / シュウの章=5)에 딱 맞게
# 공백으로 채운다. 그러지 않으면 남은 폭이 뒤 칸 패딩으로 흘러가 난도 칸이 넘친다.
_LINE_OVERRIDES = {
# 이름 칸은 전각(한글) 글리프 수가 홀수여야 한다. 레트일 'マサキの章' 은 전각이 章
# 하나뿐이라 칸 끝 phase 가 1 이고, 뒤따르는 전각 스페이서 폭이 2 가 된다.
# 짝수로 만들면 스페이서가 1 로 줄어 모자란 폭이 뒤 칸 패딩으로 흘러 난도 칸이 넘친다.
    "マサキの章難度やさしい":      "마사키 " + SPACER + " 난도 쉬움",
    "リュ-ネの章難度ふつう":       "류네편  " + SPACER + "난도 보통",
    "シュウの章難度むずかしい":    "슈우편 " + SPACER + " 난도 어려움",
    "リュ-ネの章難度ふ":           "류네편  " + SPACER + "난도 보",
    "シュウの章難度む":            "슈우편 " + SPACER + " 난도 어",
    "マサキの章ISSを使いますか?":  "마사키 " + SPACER + " ISS 사용?",
    "何それ?": "뭐야?",
}
from patch_second_exe_ui import parse_second_ui_vm_record as _pv
def _span_bytes(buf, pos):
    """레코드의 (텍스트, 바이트) 스팬 목록"""
    _e, toks = _pv(buf, pos); out = []; cur = []
    for t in toks:
        if t.kind in ("glyph", "compact_data"): cur.append(t)
        else:
            if cur: out.append(cur); cur = []
    if cur: out.append(cur)
    res = []
    for ts in out:
        txt = "".join(idx2ch.get(t.raw[0] if len(t.raw) == 1 else ((t.raw[0] - 0xEB) << 8) | t.raw[1], '')
                      for t in ts if t.kind == 'glyph')
        res.append((txt, b"".join(t.raw for t in ts)))
    return res
_lo = 0
for _k in range(MC):
    _f = MH + 4 + 4 * _k; _t = _f + s32(_f)
    try: _sp = _span_bytes(war, _t)
    except Exception: continue
    for _txt, _b in _sp:
        if _txt in _LINE_OVERRIDES and _b not in span_map:
            span_map[_b] = _LINE_OVERRIDES[_txt]; _lo += 1
if _lo: maxspan = max(len(b) for b in span_map)
print(f"  줄 전체 스팬 오버라이드 등록: {_lo}건")

recs = {}; pf = []; hits = 0
for k in range(MC):
    f = MH + 4 + 4 * k; t = f + s32(f); pf.append((f, t))
    if not (0x800 <= t < N) or t in recs: continue
    b, h = rebuild_record(t, f"ui[{k}]", allow_single=(k in {1, 7, 8, 9, 67, 76, 79}))
    # 단일 한자/2바이트 라벨(月/日/第/型/機/話/はい)은 span_map의 minL=3 때문에 안 잡힌다.
    # 그대로 두면 한글 폰트로 렌더돼 깨진다(캡처의 '떻군'류). 길이가 같은 경우에만
    # '토큰 경계에서' 치환한다 — 단순 bytes.replace는 컨트롤 인자까지 건드릴 수 있다.
    _swap = {**GLOBAL_LITERAL, **SPECIAL_SPANS.get(k, {})}
    if _swap: b = _literal_swap(b, _swap)
    hits += h; recs[t] = b
print(f"  ui_master 스팬 교체 {hits}건 / 레코드 {len(recs)}개")
pfmap = {}
for f, t in pf: pfmap.setdefault(t, []).append(f)
FOREIGN_ANCHOR_IDX = {10, 11, 12, 20, 21, 22}
anchor_t = {i: (MH + 4 + 4 * i + s32(MH + 4 + 4 * i)) for i in FOREIGN_ANCHOR_IDX}
inplace = 0; overflow_ts = []
for t in sorted(recs):
    kb = recs[t]; orig = rec_end(war, t) - t; body = kb[:-1]
    assert war[t + orig - 1] == 0xFF
    if len(body) <= orig - 1:
        war[t:t + orig - 1] = body + b"\x00" * (orig - 1 - len(body))
        for f in pfmap.get(t, ()): struct.pack_into("<i", war, f, t - f)
        inplace += 1
    else:
        overflow_ts.append(t)
for i, t in anchor_t.items():
    assert t not in overflow_ts, f"앵커 table[{i}]@{hex(t)} 넘침 — 윈도우 파괴 위험"
reloc = 0
for t in sorted(overflow_ts, key=lambda t: -len(recs[t])):
    kb = recs[t]; npos = arena_alloc(len(kb)); war[npos:npos + len(kb)] = kb
    for f in pfmap.get(t, ()): struct.pack_into("<i", war, f, npos - f)
    reloc += 1
print(f"  ui_master 제자리={inplace} 도너={reloc}")
for i, t in anchor_t.items():
    f = MH + 4 + 4 * i; assert f + s32(f) == t, f"앵커 table[{i}] 이동됨!"

# ---------------- 고정폭 자산 (THIRD와 텍스트 동일 → 한글값 재사용) ----------------
# 유닛 타입 테이블은 15엔트리 전부를 테이블에서 읽어 한자 1:1 로 바꾼다.
# (예전 하드코딩 10개짜리는 陸/空/空/水陸 을 빠뜨려 '타입' 칸이 깨져 보였다.)
import tr_extra_records as _XR
print(f"  유닛 타입 테이블 {_XR.patch_unit_types(war, RETAIL, enc_ko, idx2ch)}/15 제자리 교체")
print(f"  無有 -> 무유 {_XR.patch_yesno(war, RETAIL, enc_ko, idx2ch)}건")
cmd_ko = open(f"{_P.REPO}/third-ui/third_cmd_menu_ko.bin", "rb").read()
assert len(cmd_ko) == 76 and war[0x9747 + 76] == 0xFF, "명령 메뉴 레이아웃 이상"
war[0x9747:0x9747 + 76] = cmd_ko
print("  명령 메뉴 @0x9747 (76B) 한글")

# ---------------- 외래(포인터 없는) 맵/시스템 레코드 ----------------
# ui_master 107 포인터 테이블에 없고, 게임이 [table[N], table[N+1]) 윈도우를 훑으며 그린다.
# 맵 화면의 턴수/자금, 시스템 메뉴(페이즈종료·부대표·…), 미행동 경고, 종료 확인, 전투 방어/명중.
# EX의 5개 레코드는 제3차와 레트일 바이트가 완전히 동일 -> 제3차의 검증된 번역을 그대로 이식.
# ★패딩은 반드시 '마지막 F6 뒤(중간)'에 넣는다. 끝에 넣으면 트레일링 장식 컨트롤이 밀려
#   창 자체가 그려지지 않는다(제3차 실기에서 확인된 회귀).
_XR.patch_foreign_records(war, RETAIL, enc_ko)

# ---------------- 맵 라벨 힙: 바이트-정확 제자리 ----------------
LABEL_DELTA = 0x47c      # TR (EX는 0x484, THIRD는 0x2dc) — source_hex 유일매칭으로 실측
_labels = json.load(open(f"{_P.TRANSLATION}/second_ui_map_labels_overlay.json", encoding="utf-8"))["records"]
_lok = _lskip = _llong = 0
for _x in _labels:
    _src = bytes.fromhex(_x["source_hex"].replace(" ", "")); _ko = _x.get("korean_text")
    _o = _x["offset"] + LABEL_DELTA
    if war[_o:_o + len(_src)] != _src or not _ko or not str(_ko).strip(): _lskip += 1; continue
    _e = enc_ko(_ko)
    if len(_e) > len(_src): _llong += 1; continue
    war[_o:_o + len(_src)] = _e + b"\x00" * (len(_src) - len(_e)); _lok += 1
print(f"  map_labels 제자리={_lok} 넘침={_llong} 스킵={_lskip}")

# ---------------- 주입기가 놓쳤던 잔여 레코드 (필드상대 포인터 재조준) ----------------
# 캐릭터 사전·작품명·데모/BGM 제목, 저장/유닛강화 확인, 모노/스테레오 등.
_XR.relocate_pointed_records(war, RETAIL, enc_ko, arena_alloc)

if align_fail:
    print("\n!! 정렬 실패 (더 짧은 표현 필요):")
    for tag, jp, ko, cur, tgt in align_fail[:40]: print(f"   {tag}: '{jp}' -> '{ko}' cur={cur} target={tgt}")
    raise SystemExit(f"정렬 실패 {len(align_fail)}건")
print("  정렬: 모든 민감 스팬이 원본 advance 시그니처와 일치")

out = f"{ROOT}/test_build/tr_full/runtime/TR.WAR"
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out, "wb").write(bytes(war))
os.makedirs(f"{SP}/tr", exist_ok=True)
json.dump({"extras": EXTRAS, "donor_used": arena_used, "assets": manifest},
          open(f"{SP}/tr/tr_inject_manifest.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n도너 사용 {arena_used}/{arena_total}")
print("WROTE", out, "sha", hashlib.sha256(bytes(war)).hexdigest()[:16])
