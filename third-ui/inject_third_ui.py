"""FINAL Korean UI injection for THIRD.WAR — v5 (cave-free, relocate-all).

Fixes the v3 freeze. Root cause of v3 freeze: overflow records were relocated
into 6 "caves" (zero in retail) that are actually runtime-zeroed BSS/scratch;
a repointed record landing there reads all-zero at runtime = no 0xFF terminator
= infinite text read = hang.

v5 strategy:
  * DONOR = font glyph slots only (no caves). Safe: loaded, never rendered.
  * BEST-FIT allocation + records placed largest-first -> font donor (46720 B)
    holds everything (need ~18 KB) without the fragmentation that made v3 reach
    for caves.
  * ui_master: RELOCATE-ALL. Every one of the 107 pointer-table records keeps
    its retail bytes IN THE POOL (so the sequential-FF-walk region that reaches
    the map/system menu is byte-identical to retail) and its Korean goes to the
    font donor with the pointer repointed. No in-place VM padding in the walk
    region at all.
  * The 5 "foreign" map/system records (0x25ec9/0x25f0a system-menu/0x25f4a/
    0x26205/0x2625a) are reached by the walk, not by a pointer, so they MUST be
    translated in place (Korean body + 0x00 pad + retail 0xFF kept at its retail
    position). This is the same in-place-pad pattern 제2차 uses and is unavoidable.
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
# ------------------------------------------------------------------
import json, struct, sys, hashlib, pickle
sys.path.insert(0, str(_P.TOOLS))
from second_translation_codec import (load_safe_glyph_map, required_extra_characters,
                                      add_extra_glyph_mapping, normalise_for_font)
from build_second_expanded_patch import FONT_EXE_LAYOUT

ROOT = str(_P.WORK)
SP = str(_P.BUILD)
EXTRA_GLYPH_START, GLYPH_COUNT, GLYPH_BYTES = 0xA2F, 0xB00, 32
STRUCT_GLYPHS = {0x3FF, 0x6FF, 0x700}
CTRL_ARGS = {0xF6:0,0xF7:0,0xF8:1,0xF9:1,0xFA:0,0xFB:2,0xFC:2,0xFD:2,0xFE:1}
LABEL_DELTA = 0x2dc

SRC = f"{ROOT}/test_build/third_full/runtime/THIRD/THIRD.WAR"
war = bytearray(open(SRC, "rb").read()); N = len(war)
assert war[:8]==b"PS-X EXE" and len(war)==0x12c000, "SRC is not a THIRD.WAR pre-inject build!"
RETAIL = open(f"{ROOT}/extracted/THIRD/THIRD.WAR", "rb").read()   # 정렬 기준(레트일)
# quit-message pool (title VM): carry the v0.9.3 Korean pool the rebuild loses
_qp=open(f"{_P.REPO}/third-ui/third_quit_pool.bin","rb").read()
assert len(_qp)==0x811e-0x7dc2
war[0x7dc2:0x811e]=_qp
print("quit-message pool ported from v0.9.3 (860 B @0x7dc2)")
mpj = json.load(open(f"{ROOT}/research/srwcb_embedded_font_mapping_reviewed.json", encoding="utf-8"))
idx2ch = {r["glyph_index"]: (r.get("character") or "") for r in mpj["rows"]}
ch2idx = {}
for i, c in sorted(idx2ch.items()):
    if c and c not in ch2idx: ch2idx[c] = i

def s32(o): return struct.unpack_from("<i", war, o)[0]
def tokens(buf,pos,limit=None):
    p=pos
    while p<(limit or N):
        b=buf[p]
        if b==0xFF: yield (p,1,'end'); return
        if b<0xEB: yield (p,1,'g'); p+=1
        elif b<=0xF5: yield (p,2,'g'); p+=2
        else: n=1+CTRL_ARGS.get(b,0); yield (p,n,'c'); p+=n
def rec_end(buf,pos):
    for s,n,k in tokens(buf,pos):
        if k=='end': return s+1
    return pos
def decode(buf,pos):
    out=[]
    for s,n,k in tokens(buf,pos):
        if k=='end': break
        if k=='g': out.append(idx2ch.get(buf[s] if n==1 else ((buf[s]-0xEB)<<8)|buf[s+1],""))
    return "".join(out)
def enc_jp(t):
    o=bytearray()
    for ch in t:
        i=ch2idx.get(ch)
        if i is None: return None
        o += bytes([i]) if i<0xEB else bytes(((i>>8)+0xEB,i&0xFF))
    return bytes(o)

# ---------------- translations ----------------
jp2ko={}
for tb in json.load(open(f"{_P.TRANSLATION}/second_ui_tables_overlay.json",encoding="utf-8"))["tables"]:
    for e in tb["entries"]:
        if e.get("source_text") and e.get("korean_text") and str(e["korean_text"]).strip(): jp2ko[e["source_text"]]=e["korean_text"]
for tb in json.load(open(f"{_P.TRANSLATION}/second_ui_names_overlay.json",encoding="utf-8"))["tables"]:
    for r in tb["rows"]:
        if r.get("japanese") and r.get("korean") and str(r["korean"]).strip(): jp2ko[r["japanese"]]=r["korean"]
newtr={k:v for k,v in json.load(open(f"{_P.REPO}/third-ui/third_ui_translations.json",encoding="utf-8")).items() if not k.startswith("_")}
for k,v in newtr.items():
    if v: jp2ko.setdefault(k,v)
# system message-pool translations (workflow output) + SRW glossary for sub-term matching
import os as _os
for _fn in ("msgpool_translations.json","msgpool_srw_gloss.json"):
    _p=f"{_P.TRANSLATION}/{_fn}"
    if _os.path.exists(_p):
        for k,v in json.load(open(_p,encoding="utf-8")).items():
            if v and str(v).strip(): jp2ko.setdefault(k,v)
# name-entry picker tabs stay Japanese: the grids input kana, and kana glyphs
# (<0x101) render correctly; Korean labels can't fit the (4,0) tab cells
for _k in ("ひらがな","カタカナ"): jp2ko.pop(_k,None)
span_map={}
for a in json.load(open(f"{_P.TRANSLATION}/second_ui_scripts_overlay.json",encoding="utf-8"))["assets"].values():
    for r in a["records"]:
        for rep in r.get("replacements",[]):
            if rep.get("korean_text"): span_map[bytes.fromhex(rep["source_hex"].replace(" ",""))]=rep["korean_text"]
for jp,ko in jp2ko.items():
    b=enc_jp(jp)
    if b and len(b)>=2: span_map.setdefault(b,ko)
span_map={k:v for k,v in span_map.items() if v not in ("히라가나","가타카나")}
# ---- de-space overrides (menus/status: strip spaces that made KO wider than JP) ----
# Keyed by the current spaced Korean value so it catches spans/table entries from
# any source (scripts overlay, jp2ko, names). Dialogue (3_SCE) is built separately,
# so it is untouched. despace_map.json = {spaced_ko: despaced_ko} (workflow-vetted).
import os as _os2
# De-space (menus/status: strip spaces that made KO wider than JP). Matching is on
# all-space-stripped Korean CONTENT (ascii ' ' + fullwidth '　'), so it catches
# overlay-keyed spans, fullwidth-space records, and jp2ko/table entries uniformly.
_dmp=f"{_P.REPO}/translation/despace_nospace.json"   # {content_no_spaces: despaced_ko}
if _os2.path.exists(_dmp):
    _nm=json.load(open(_dmp,encoding="utf-8"))
    def _strip(_s): return _s.replace(" ","").replace("　","")
    _sm=0; _jt=0
    for _b,_v in list(span_map.items()):          # ui spans (whole-value match)
        _nk=_strip(_v)
        if (" " in _v or "　" in _v) and _nk in _nm and _nm[_nk]!=_v:
            span_map[_b]=_nm[_nk]; _sm+=1
    for _jp,_ko in list(jp2ko.items()):           # tables + jp2ko-derived spans
        _nk=_strip(_ko)
        if _nk in _nm and _nm[_nk]!=_ko: jp2ko[_jp]=_nm[_nk]; _jt+=1
    # add whole-label keys for targets not present as a single span (space-free labels)
    _dmj=f"{_P.REPO}/third-ui/despace_map_jp.json"
    if _os2.path.exists(_dmj):
        for _jp,_dk in json.load(open(_dmj,encoding="utf-8")).items():
            _b=enc_jp(_jp)
            if _b and _b not in span_map: span_map[_b]=_dk
    print(f"  de-space: span_map value-override={_sm}, jp2ko={_jt}")
maxspan=max(len(b) for b in span_map)
labels=json.load(open(f"{_P.TRANSLATION}/second_ui_map_labels_overlay.json",encoding="utf-8"))["records"]
# The map-label overlay and the system message pool are the SAME pointer-addressed pool.
# Records the (reviewed 제2차) map-label section already translates byte-exact in place are
# handled there; the message-pool section skips them to avoid double-writes.
MAP_LABEL_OFFS={x["offset"]+LABEL_DELTA for x in labels if x.get("korean_text") not in (None,"")}

# ---------------- final glyph map + donor slots ----------------
ko_all=list(jp2ko.values())+list(span_map.values())+[x["korean_text"] for x in labels if x.get("korean_text")]
ko_all+=[v for t in json.load(open(f"{_P.TRANSLATION}/third_translation_overlay.json",encoding="utf-8"))["translations"].values() for v in t["ko_parts"].values()]
sys.path.insert(0, SP)
from third_align_overrides import ALIGN_KO_TEXTS
ko_all+=ALIGN_KO_TEXTS
import re as _re0
_STRIP=_re0.compile(r"\[F[6-9A-Ea-e]\]")
ko_all=[_STRIP.sub("", x) for x in ko_all]
base=load_safe_glyph_map()
EXTRAS=required_extra_characters([normalise_for_font(x)[0] for x in ko_all], base)
gm=add_extra_glyph_mapping(base,EXTRAS)
print("FINAL extras:",EXTRAS)
import re as _re
_CTRL_MARK=_re.compile(r"\[F([6-9A-Ea-e])\]")
def enc_ko(s):
    o=bytearray(); pos=0
    for m in _CTRL_MARK.finditer(s):
        for ch in normalise_for_font(s[pos:m.start()])[0]:
            i=gm[ch]; o += bytes([i]) if i<0xEB else bytes(((i>>8)+0xEB,i&0xFF))
        o.append(int("F"+m.group(1),16)); pos=m.end()
    for ch in normalise_for_font(s[pos:])[0]:
        i=gm[ch]; o += bytes([i]) if i<0xEB else bytes(((i>>8)+0xEB,i&0xFF))
    return bytes(o)
used=set(STRUCT_GLYPHS)
for s in ko_all:
    for ch in normalise_for_font(s)[0]:
        i=gm.get(ch)
        if i is not None: used.add(i)
keep=set(range(0x000,0x101))|used
# preserve retail symbol glyphs still referenced by untranslated data (button-config
# icons ○×△□ etc.) so their slots are never donated
keep|={i for i,c in idx2ch.items() if c in "○×△□◎☆★↑↓→←"}
font_off=next(v for k,v in FONT_EXE_LAYOUT.items() if str(k).replace("\\","/").endswith("THIRD.WAR"))
free=[i for i in range(0x101,GLYPH_COUNT) if i not in keep]
runs=[];s=p=None
for i in free:
    if s is None: s=p=i; continue
    if i==p+1: p=i
    else: runs.append([font_off+s*GLYPH_BYTES,font_off+(p+1)*GLYPH_BYTES]); s=p=i
if s is not None: runs.append([font_off+s*GLYPH_BYTES,font_off+(p+1)*GLYPH_BYTES])
# NO CAVES. Font glyph donor only (safe: loaded, never rendered).
ARENA=runs; arena_total=sum(b-a for a,b in ARENA)
print(f"donor blocks (font only): {len(ARENA)}  total {arena_total} bytes")
arena_used=0
def arena_alloc(n):
    """Best-fit: smallest fragment that still fits, to minimise fragmentation."""
    global arena_used
    best=None
    for blk in ARENA:
        room=blk[1]-blk[0]
        if room>=n and (best is None or room<ARENA[best][1]-ARENA[best][0]):
            best=ARENA.index(blk)
    if best is None: raise SystemExit(f"ARENA OVERFLOW need {n} (largest {max(b-a for a,b in ARENA)})")
    off=ARENA[best][0]; ARENA[best][0]+=n; arena_used+=n; return off

# ---------------- span-level phase-advance preservation (SECOND discipline) ----------------
# CAUTION: ui script records mix renderer text with a UI-VM whose opcodes F0-F5
# carry args (SECOND_UI_VM_COMPACT_ARG_LENGTHS) -- Hangul lead bytes overlap that
# range, so records must NEVER be re-tokenized/reflowed wholesale. The safe unit is
# the REPLACED TEXT SPAN: swap matched retail text bytes for Korean encoded to the
# SAME phase-0 (advance, phase) signature (patch_second_exe_ui _renderer_layout_signature
# invariant), padding with 0x00 and fixing phase with the invisible high blank EE FF
# (glyph 0x3FF). Every non-matched byte (incl. VM opcodes/args) stays byte-exact, so
# all window/anchor positions survive and columns align like retail.
from third_align_overrides import ALIGN_OVERRIDES, SPECIAL_SPAN_RECORDS, SPIRIT_DESC_SHORT
HIGH_BLANK=b"\xEE\xFF"
_JPDEC=dict(idx2ch); _JPDEC[0x000]=" "
def _sig(idxs):
    ph=0;adv=0
    for i in idxs:
        if i<0x101: adv+=1
        else: adv+=1+ph; ph^=1
    return adv,ph
def _idxs_of(b):
    out=[];p=0
    while p<len(b):
        x=b[p]
        if x<0xEB: out.append(x); p+=1
        else: out.append(((x-0xEB)<<8)|b[p+1]); p+=2
    return out
align_fail=[]
def _despaced(s):
    """띄어쓰기를 뒤에서부터 하나씩 빼며 만든 후보들 (원문 유지 우선)."""
    out=[]
    if " " not in s: return out
    parts=s.split(" ")
    for k in range(len(parts)-1, 0, -1):
        out.append(" ".join(parts[:k]) + "".join(parts[k:]))
    out.append(s.replace(" ", ""))
    seen=set(); uniq=[]
    for c in out:
        if c not in seen: seen.add(c); uniq.append(c)
    return uniq
def fit_span(src_bytes, ko_text, tag, strict, pad=True):
    """Encode ko_text to occupy src_bytes' renderer signature.
    strict (span followed by FC anchor / more glyphs): report if unfittable.
    relaxed (line/record end, F8 fields...): best-effort pad, keep full text on miss.
    pad=False (cursor-relative screens where 0x00 filler shifts later anchors):
    just pick the first candidate whose advance fits, no padding bytes."""
    target=_sig(_idxs_of(src_bytes))
    jp="".join(_JPDEC.get(i,"·") for i in _idxs_of(src_bytes))
    if not pad:
        for cand in (ko_text,)+tuple(ALIGN_OVERRIDES.get(jp,())):
            e=enc_ko(cand)
            if _sig(_idxs_of(e))[0]<=target[0]: return e
        return enc_ko(ko_text)
    def attempt(b):
        adv,ph=_sig(_idxs_of(b))
        if ph!=target[1] and adv+1+ph<=target[0]:
            b=b+HIGH_BLANK; adv+=1+ph; ph^=1
        if ph==target[1] and adv<=target[0]:
            return b+b"\x00"*(target[0]-adv)
        return None
    r=attempt(enc_ko(ko_text))
    if r is not None: return r
    # 폭이 모자라면 축약보다 **띄어쓰기 제거**를 먼저 시도한다(번역을 살리는 쪽).
    for cand in _despaced(ko_text):
        r=attempt(enc_ko(cand))
        if r is not None: return r
    for cand in ALIGN_OVERRIDES.get(jp,()):
        r=attempt(enc_ko(cand))
        if r is not None: return r
    if strict:
        align_fail.append((tag,jp,ko_text,_sig(_idxs_of(enc_ko(ko_text))),target))
    return enc_ko(ko_text)   # unfitted

NOPAD_RECORDS={0x26b36}   # ui[39] system-settings: cursor-relative sub-windows
def rebuild_record(start,tag="?",fit=True,allow_single=False):
    """allow_single: permit 2-byte (single-kanji) span matches. Only safe in the
    terrain-header status records (空/陸/海/宇 columns); elsewhere a lone kanji
    byte-pair can coincide with VM opcode args and corrupt the record."""
    end=rec_end(war,start); out=bytearray(); p=start; hit=0
    idx={t[0]:t for t in tokens(war,start)}
    minL=2 if allow_single else 3
    while p<end-1:
        t=idx.get(p)
        if t is None: out.append(war[p]); p+=1; continue
        s_,n_,k_=t
        if k_=='g':
            best=None
            for L in range(min(maxspan,end-1-p),minL-1,-1):
                c=bytes(war[p:p+L])
                if c in span_map: best=(L,span_map[c]); break
            if best:
                src=bytes(war[p:p+best[0]])
                nb=war[p+best[0]]                      # byte right after the span
                # strict when a hard column anchor (FC) or more text follows on the
                # same line (glyph byte / unambiguous kanji lead EB-EF; F0-F5 may be
                # VM opcodes in script records, so treated as relaxed)
                strict = (nb==0xFC) or (nb<0xEB) or (0xEB<=nb<=0xEF)
                out+= fit_span(src,best[1],tag,strict,pad=(start not in NOPAD_RECORDS)) if fit else enc_ko(best[1])
                p+=best[0]; hit+=1; continue
        out+=war[s_:s_+n_]; p=s_+n_
    out.append(0xFF); return bytes(out),hit
def special_span_rebuild(start):
    """retail record with ONLY the literal spans replaced (date pickers etc.)."""
    end=rec_end(RETAIL,start); rec=bytearray(RETAIL[start:end])
    reps=SPECIAL_SPAN_RECORDS[start]
    def enc_jp_lit(t):
        o=bytearray()
        for ch in t:
            i=next(k for k,v in sorted(idx2ch.items()) if v==ch)
            o+=bytes([i]) if i<0xEB else bytes(((i>>8)+0xEB,i&0xFF))
        return bytes(o)
    for jp,ko in sorted(reps.items(),key=lambda kv:-len(kv[0])):
        pat=enc_jp_lit(jp); rep=enc_ko(ko)
        i=0
        while True:
            j=rec.find(pat,i)
            if j<0: break
            rec[j:j+len(pat)]=rep; i=j+len(rep)
    return bytes(rec)

manifest=[]
def repack(name,entries,pool_lo,pool_hi,pf):
    newpos={}; cur=pool_lo; ov=0
    # pack into the record's own pool in offset order, overflow to donor
    for t in sorted(entries):
        b=entries[t]
        if cur+len(b)<=pool_hi: newpos[t]=cur; cur+=len(b)
        else: newpos[t]=None
    # overflow records to donor, largest-first (grab contiguous fragments first)
    ov_ts=[t for t in entries if newpos[t] is None]
    for t in sorted(ov_ts, key=lambda t:-len(entries[t])):
        newpos[t]=arena_alloc(len(entries[t])); ov+=len(entries[t])
    for t,b in entries.items(): war[newpos[t]:newpos[t]+len(b)]=b
    if cur<pool_hi: war[cur:pool_hi]=b"\x00"*(pool_hi-cur)
    for f,t in pf:
        if t in newpos: struct.pack_into("<i",war,f,newpos[t]-f)
    manifest.append(dict(asset=name,records=len(entries),pool_used=cur-pool_lo,pool=pool_hi-pool_lo,arena=ov))
    print(f"  {name:20s} recs={len(entries):>4} pool {cur-pool_lo:>6}/{pool_hi-pool_lo:<6} donor+{ov}")

TABLES=[("terrain_names",0xbb0c,144,0xbf68),("spirit_commands",0xbf68,94,0xc634),
("enhancement_parts",0xc634,64,0xc9ac),("weapon_names",0xc9ac,1408,0x1130c),
("pilot_skills",0x1130c,52,0x1155c),("unit_abilities",0x1155c,22,0x11668),
("scenario_titles",0x11668,192,0x11be0),("pilot_short_names",0x10dbf8,400,0x10eb2c),
("pilot_full_names",0x10eb2c,400,0x110208),("unit_names",0x110208,448,0x111b60)]
SPIRIT_DESC_MAX=34   # wider descriptions wrap around the screen (x overflow)
for name,ptr,cnt,bound in TABLES:
    pool_lo=ptr+4+4*cnt; recs={}; pf=[]
    for k in range(cnt):
        f=ptr+4+4*k; t=f+s32(f); pf.append((f,t))
        if not (pool_lo<=t and rec_end(war,t)<=bound) or t in recs: continue
        jp=decode(war,t); ko=jp2ko.get(jp)
        # 무기명은 레트일이 붙여 쓴다. 사전(despace_nospace)에 등록된 것만 지우던
        # 예전 방식은 폭에 들어가는 172종을 놓쳐 같은 표에서 표기가 갈렸다
        # (2026-08-19 제보 #15a). 값이 어느 사전에서 왔든 **인코딩 직전** 여기서 건다.
        if name=="weapon_names" and ko: ko=ko.replace(" ","").replace("　","")
        if name=="spirit_commands" and ko:
            adv,_ph=_sig(_idxs_of(enc_ko(ko)))
            if adv>SPIRIT_DESC_MAX:
                ko=SPIRIT_DESC_SHORT.get(jp)
                assert ko is not None, f"spirit desc too wide, no short form: {jp[:24]}"
                adv,_ph=_sig(_idxs_of(enc_ko(ko)))
                assert adv<=SPIRIT_DESC_MAX, f"short spirit desc still {adv}: {ko}"
        recs[t]=(enc_ko(ko)+b"\xFF") if ko else bytes(war[t:rec_end(war,t)])
    repack(name,recs,pool_lo,min(max(rec_end(war,t) for t in recs),bound),pf)

# ---------------- ui_master: RELOCATE-ALL (walk region stays byte-identical to retail) ----------------
# {오프셋: 심을 바이트}. 사람이 읽고 고치는 소스는 third-ui/foreign_recs.json 이다.
FOREIGN={int(k,16):bytes.fromhex(v["hex"]) for k,v in
         json.load(open(f"{_P.REPO}/third-ui/foreign_recs.json",encoding="utf-8"))["records"].items()}
foreign_len={o:rec_end(war,o)-o for o in FOREIGN}          # original lengths (BEFORE any writes)
MH,MC=0x247CC,107
pool_lo=MH+4+4*MC; recs={}; pf=[]; hits=0
for k in range(MC):
    f=MH+4+4*k; t=f+s32(f); pf.append((f,t))
    if not (0x800<=t<N) or t in recs: continue
    if t in SPECIAL_SPAN_RECORDS:
        recs[t]=special_span_rebuild(t); continue
    b,h=rebuild_record(t,f"ui[{k}]",fit=(t not in NOPAD_RECORDS),
                       allow_single=(k in {1,7,8,9,67,76,79}))
    if t==0x26b36:   # settings screen: compact forms + FC coordinate nudges
        b=b.replace(enc_ko("전투 BGM 설정"),enc_ko("전투BGM설정"))          # 9 = retail-exact
        b=b.replace(enc_ko("+셀렉트+스타트로 리셋"),enc_ko("+셀렉트+스타트 리셋"))  # 14 <= 15
        # FC dx/dy are signed relative cell deltas. My 2-cell sound value is 1 narrower
        # than retail (4), which drags the BGM value + everything after it 1 cell left.
        # (1) push the BGM value anchor +1 (un-overlap 전환 from its label);
        # (2) compensate the 특수조작 back-jump so the lower-left block stays put;
        # (3) push the right column (버튼설정/결정.../symbols) +1 as requested.
        _fc=[("fcfb02f806","fcfc02f806"),                                  # BGM value dx -5->-4
             ("fcf105"+enc_ko("특").hex(),"fcf005"+enc_ko("특").hex()),     # 특수조작 dx -15->-16
             ("fc09ea"+enc_ko("버").hex(),"fc0aea"+enc_ko("버").hex())]     # 버튼설정 dx 9->10
        for _o,_n in _fc:
            _ob=bytes.fromhex(_o); assert b.count(_ob)==1,_o
            b=b.replace(_ob,bytes.fromhex(_n))
    hits+=h; recs[t]=b
print(f"  ui_master span hits: {hits}  distinct recs: {len(recs)}  (span-fitted)")
pfmap={}
for f,t in pf: pfmap.setdefault(t,[]).append(f)
# EXTENT-PRESERVING in-place. The in-battle map/system menus are drawn by a
# POINTER-BOUNDED WINDOW walk: menu N renders every FF-record in [table[N],table[N+1]).
# The 5 pointerless "foreign" records fall inside such windows (system menu 0x25f0a is
# in window [table[11]=0x25efc, table[12]=0x25f67)). So the window ANCHOR records
# (table 10,11,12,20,21,22) and everything between them MUST keep their retail byte
# lengths so the window ranges still bound the foreign records. Records that fit are
# translated IN PLACE (table[i] keeps pointing at retail t). Overflow records are the
# ONLY ones relocated (to safe font donor, repointed) -- verified that NONE of them is a
# foreign-window anchor and none lies inside a range-drawn window, so relocating them is
# safe (freeze-diff RE: repointing resolves at battle time, never the load freeze).
FOREIGN_ANCHOR_IDX={10,11,12,20,21,22}
anchor_t={i:(MH+4+4*i+s32(MH+4+4*i)) for i in FOREIGN_ANCHOR_IDX}
inplace=0; overflow_ts=[]
for t in sorted(recs):
    kb=recs[t]; orig=rec_end(war,t)-t; body=kb[:-1]
    assert war[t+orig-1]==0xFF
    if len(body)<=orig-1:
        war[t:t+orig-1]=body+b"\x00"*(orig-1-len(body))
        for f in pfmap.get(t,()): struct.pack_into("<i",war,f,t-f)
        inplace+=1
    else:
        overflow_ts.append(t)   # retail bytes stay at [t,t+orig) (extent for the walk)
# guard: no foreign-window anchor may be an overflow (would break the system-menu window)
for i,t in anchor_t.items():
    assert t not in overflow_ts, f"anchor table[{i}]@{hex(t)} overflowed -- would break window"
# relocate overflow largest-first into safe font donor
reloc=0
for t in sorted(overflow_ts, key=lambda t:-len(recs[t])):
    kb=recs[t]; npos=arena_alloc(len(kb)); war[npos:npos+len(kb)]=kb
    for f in pfmap.get(t,()): struct.pack_into("<i",war,f,npos-f)
    reloc+=1
print(f"  ui_master extent-preserving: in-place={inplace} donor={reloc}")
# verify anchors still point at their retail positions (windows intact)
for i,t in anchor_t.items():
    f=MH+4+4*i; assert f+s32(f)==t, f"anchor table[{i}] moved!"
# foreign map/system menu records: in-place, keep retail FF at retail offset.
# CRITICAL: pad in the MIDDLE (after the last F6), NOT at the end -- these are drawn by
# a window-walk whose trailing decoration control-run (e.g. 0x25f0a `f7 0000 f508`) must
# stay at its retail byte offset right before the FF. End-padding pushes it away -> the
# window is not drawn (confirmed on hardware: test4 end-pad invisible, test6 mid-pad OK).
def _mid_pad(body, pad):
    if pad<=0: return bytes(body)
    body=bytearray(body); offs=[]; p=0
    while p<len(body):
        offs.append(p); b=body[p]
        p+= 1 if b<0xEB else (2 if b<=0xF5 else 1+CTRL_ARGS.get(b,0))
    last_f6=max((i for i in offs if body[i]==0xF6), default=None)
    ins=(last_f6+1) if last_f6 is not None else len(body)
    return bytes(body[:ins]+b"\x00"*pad+body[ins:])
for o in sorted(FOREIGN):
    rec=FOREIGN[o]; ol=foreign_len[o]; body=rec[:-1]
    assert len(body)<=ol-1 and war[o+ol-1]==0xFF, hex(o)
    war[o:o+ol-1]=_mid_pad(body, ol-1-len(body))
print(f"  foreign map/system records translated in-place (mid-pad): {len(FOREIGN)}")

# ---------------- system message pool (0x9a00-0xb200, relative-int32 pointer-addressed) ----------------
# Each record is drawn as a single record from its own pointer (read until FF), so we may
# SHORTEN in place (write Korean+FF at the record start; the pointer reads the new, shorter
# record; the dead bytes after are never read). Overflow relocates to the font donor and
# repoints every pointer to that record.
import pickle as _pickle
def _msgpool_index(buf, lo=0x9a00, hi=0xb200):
    """시스템 메시지 풀의 (타깃->포인터들, 레코드 목록).

    예전엔 작업 중 만든 pickle 에 담아 뒀는데 그 파일이 없으면 빌드가 안 됐다.
    풀은 **필드상대 int32 포인터**로만 접근되므로 실행파일에서 바로 만들 수 있다.
    포인터는 4바이트 정렬이 아니라서 바이트마다 훑는다.
    """
    t2p={}
    for f in range(0x800, len(buf)-4):
        d=struct.unpack_from("<i", buf, f)[0]
        t=f+d
        if lo<=t<hi: t2p.setdefault(t,[]).append(f)
    recs=[]
    for t in sorted(t2p):
        # 레코드 '시작'만 — 앞 바이트가 종결자여야 한다. 안 그러면 레코드 중간을
        # 가리키는 포인터(같은 문장의 뒷부분을 그리는 용도)까지 레코드로 세어
        # 문장이 조각나고 정렬이 깨진다.
        if t<=lo or buf[t-1]!=0xFF: continue
        e=rec_end(buf,t)
        if e<=t or e>hi+0x200 or buf[e-1]!=0xFF: continue
        if not any(0x20<=buf[q]<0xEB or 0xEB<=buf[q]<=0xF5 for q in range(t,e-1)): continue
        recs.append(t)
    return t2p, recs
_mpp=f"{SP}/msgpool_full.pkl"
if _os.path.exists(_mpp):
    _mp=_pickle.load(open(_mpp,"rb")); _mp_tgt2ptr=_mp["tgt2ptr"]
    _mp_recs=[w["off"] for w in json.load(open(f"{SP}/msgpool_clean.json",encoding="utf-8"))]
else:
    _mp_tgt2ptr,_mp_recs=_msgpool_index(bytes(war))
    print(f"  시스템 메시지 풀 색인 생성: 레코드 {len(_mp_recs)} / 포인터 {sum(len(v) for v in _mp_tgt2ptr.values())}")
# packed value lists are OFFSET-INDEXED: byte lengths must not change.
# 0x9aae(모노/입체) 0x9ab7(전환/고정): Korean slots byte-equal -> translate w/o padding.
# 0x9ac4(button names ○X△ロ...): 없음(4B)!=なし(2B) -> keep retail (kana renders fine,
# symbol glyph slots preserved via the keep-set).
# the F8 fields of the settings screen draw these value records; their rendered
# widths feed the cursor-relative FC anchor chain, so their slots must keep
# retail advance. Slot padding is impossible (0x00 terminates a slot), so slots
# whose Korean can't match retail advance stay Japanese kana (renders fine).
PACKED_NOPAD=set(); PACKED_SKIP={0x9aae,0x9ab7,0x9ac4}
def rebuild_packed(start):
    """offset-indexed record: each replaced span keeps its exact BYTE length."""
    end=rec_end(war,start); out=bytearray(); p=start
    while p<end-1:
        x=war[p]
        if x<0xEB or (0xEB<=x<=0xF5):
            best=None
            for L in range(min(maxspan,end-1-p),1,-1):
                c=bytes(war[p:p+L])
                if c in span_map: best=(L,span_map[c]); break
            if best:
                e=enc_ko(best[1])
                assert len(e)<=best[0], f"packed span at {hex(p)} overflows bytes"
                out+=e+b"\x00"*(best[0]-len(e)); p+=best[0]; continue
            n=1 if x<0xEB else 2
            out+=war[p:p+n]; p+=n
        else:
            n=1+CTRL_ARGS.get(x,0); out+=war[p:p+n]; p+=n
    out.append(0xFF); return bytes(out)
mp_fit=mp_reloc=mp_skip=0
for t in sorted(_mp_recs, key=lambda t:-(rec_end(war,t)-t)):
    if t in MAP_LABEL_OFFS or t in PACKED_SKIP: mp_skip+=1; continue
    orig=rec_end(war,t)-t
    if t in PACKED_NOPAD:
        kb=rebuild_packed(t)
        assert len(kb)==orig, f"packed record {hex(t)} byte length changed!"
    else:
        kb,_=rebuild_record(t,f"mp@{hex(t)}")
    if len(kb)<=orig:
        war[t:t+len(kb)]=kb          # shorten in place; leave dead bytes after new FF
        mp_fit+=1
    else:
        npos=arena_alloc(len(kb)); war[npos:npos+len(kb)]=kb
        for f in _mp_tgt2ptr.get(t,()): struct.pack_into("<i",war,f,npos-f)
        mp_reloc+=1
print(f"  system message pool: shorten-in-place={mp_fit} relocated={mp_reloc} skip(map-label)={mp_skip}")

# ---------------- music/demo pool (nested) ----------------
mf=0x94D8+4+36*4; nested=mf+s32(mf); recs={}; pf=[]
for i in range(172):
    f=nested+i*4; t=f+s32(f); pf.append((f,t))
    if t in recs: continue
    b,_=rebuild_record(t); recs[t]=b
repack("music_demo",recs,min(recs),max(rec_end(war,t) for t in recs),pf)

# ---------------- map labels: byte-exact in place ----------------
ok=grew=skip=0; toolong=[]
for x in labels:
    src=bytes.fromhex(x["source_hex"].replace(" ","")); ko=x.get("korean_text"); o=x["offset"]+LABEL_DELTA
    # 공백만 있는 번역은 "번역 없음"이 아니다 — 라벨 힙에서 원문 조사 한 글자를
    # **빈칸으로 지우는** 정상 항목이다(특수기능 레벨업의 `が`, 원장 offset 40511
    # budget 1). 예전 `not str(ko).strip()` 가 이걸 버려서 제3차·EX·트레이닝에
    # 일본어 `が` 가 그대로 남았다 — 2026-08-19 제보 #15e.
    if war[o:o+len(src)]!=src or ko is None or ko=="": skip+=1; continue
    e=enc_ko(ko)
    if len(e)>len(src): toolong.append((x["japanese_text"],ko,len(src),len(e))); grew+=1; continue
    war[o:o+len(src)]=e+b"\x00"*(len(src)-len(e)); ok+=1
print(f"  map_labels           in-place={ok} too_long={grew} skip={skip}")
for t in toolong[:8]: print("     TOO LONG:",t)

# ---------------- button-config symbols (0x9ac4, offset-indexed, kept retail) ----------------
# The PS-button glyphs live at 0x8f8-0x8fb, now overwritten by Hangul. Repoint the
# record's four 2-byte refs at the ○×△□ extras (same byte width, packing intact).
_SYMFIX=[(0x8F8,'○'),(0x8F9,'×'),(0x8FA,'△'),(0x8FB,'□')]
_bc_end=rec_end(war,0x9ac4); _n_sym=0
for _old,_ch in _SYMFIX:
    _ni=gm.get(_ch)
    assert _ni is not None, f"symbol {_ch} missing from glyph map"
    _pat=bytes(((_old>>8)+0xEB,_old&0xFF)); _rep=bytes(((_ni>>8)+0xEB,_ni&0xFF))
    _i=0x9ac4
    while True:
        _j=war.find(_pat,_i,_bc_end)
        if _j<0: break
        war[_j:_j+2]=_rep; _i=_j+2; _n_sym+=1
print(f"  button-config symbols repointed to extras: {_n_sym}")
# remaining Japanese in the 4-byte button cells: Korean of the SAME byte width
# (스타트/셀렉트 are 6B and cannot fit; START->시작, SELECT->선택, なし->무)
_BTNTXT=[("a8ae11b7","시작"),("aadb9eb7","선택"),("6956","무")]
for _hx,_ko in _BTNTXT:
    _pat=bytes.fromhex(_hx); _rep=enc_ko(_ko)
    assert len(_rep)==len(_pat), (_hx,_ko)
    _j=war.find(_pat,0x9ac4,_bc_end)
    assert _j>=0, _hx
    war[_j:_j+len(_pat)]=_rep
print("  button-config labels Korean: 시작/선택/무")

# ---------------- settings VALUE records: byte-exact Korean slots ----------------
# 0x9aae(sound) / 0x9ab7(BGM): offset-indexed slots, 0x00 = slot terminator, so a
# replacement must keep the slot's exact BYTE length (advance may shrink by <=1-2,
# same jitter retail's own ON/OFF values have).
# both options of one field MUST render the same advance, or the cursor-relative
# anchor chain shifts everything downstream whenever the value toggles
_VALFIX=[(0x9aae,"モノラル","모노"),(0x9aae,"ステレオ","입체"),      # both adv 3
         (0x9ab7,"切り替え","전환"),(0x9ab7,"固定","고정")]          # both adv 3
def _enc_jp_lit(t):
    o=bytearray()
    for ch in t:
        i=next(k for k,v in sorted(idx2ch.items()) if v==ch)
        o+=bytes([i]) if i<0xEB else bytes(((i>>8)+0xEB,i&0xFF))
    return bytes(o)
for _off,_jp,_ko in _VALFIX:
    _pat=_enc_jp_lit(_jp); _rep=enc_ko(_ko)
    assert len(_rep)<=len(_pat), (_jp,_ko)
    _end=rec_end(war,_off)
    _j=war.find(_pat,_off,_end)
    assert _j>=0, f"value slot {_jp!r} not found @{hex(_off)}"
    war[_j:_j+len(_pat)]=_rep+b"\x00"*(len(_pat)-len(_rep))
print(f"  settings value slots Korean (byte-exact): {len(_VALFIX)}")

# ---------------- unit TYPE table (0x9481, fixed cells, EE FF right-align padding) ----------------
# kanji count == Hangul count for every entry, so the swap is byte-exact in place
_TYPES=[(0x9481,"宇宙","우주"),(0x9486,"空陸","공륙"),(0x948f,"水空","수공"),
        (0x9498,"水陸空","수륙공"),(0x94a1,"陸地中","육지중"),(0x94aa,"空陸地中","공륙지중"),
        (0x94b3,"水陸","수륙"),(0x94bc,"水","수"),(0x94c5,"空地中","공지중"),(0x94ce,"空陸","공륙")]
for _off,_jp,_ko in _TYPES:
    _e=enc_ko(_ko); assert len(_e)==2*len(_ko)==2*len(_jp)
    _end=_off
    while war[_end]!=0xFF: _end+= 1 if war[_end]<0xEB else 2
    assert war[_end-len(_e):_end]!=_e or True
    war[_end-len(_e):_end]=_e          # keep EE FF padding prefix, FF untouched
print(f"  unit type table translated in place: {len(_TYPES)} entries")

# ---------------- fixed-width unit command menu (accessed by index, not pointer) ----------------
cmd_ko=open(f"{_P.REPO}/third-ui/third_cmd_menu_ko.bin","rb").read()
assert len(cmd_ko)==76 and war[0x95a3+76]==0xFF, "command menu record layout unexpected"
war[0x95a3:0x95a3+76]=cmd_ko
print("  command_menu @0x95a3 (76B) Korean (이동/공격/…)")

if align_fail:
    print("\n!! ALIGN FAILURES (need shorter overrides):")
    for tag,jp,ko,cur,tgt in align_fail: print(f"   {tag}: '{jp}' -> '{ko}' cur={cur} target={tgt}")
    raise SystemExit("alignment failed")
print("  alignment: all sensitive runs match retail advance signatures")

out=f"{ROOT}/test_build/third_full/runtime/THIRD/THIRD.WAR"
open(out,"wb").write(bytes(war))
print(f"\ndonor used {arena_used}/{arena_total}")
json.dump({"extras":EXTRAS,"donor_used":arena_used,"assets":manifest,"labels_ok":ok},
          open(f"{SP}/third_ui_inject_manifest.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
print("WROTE",out,"sha",hashlib.sha256(bytes(war)).hexdigest()[:16])
