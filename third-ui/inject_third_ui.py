"""FINAL Korean UI injection for THIRD.WAR.

Donor space = font glyph slots that no final Korean text references (unused
KS X 1001 syllables + the extra-glyph tail). Slots in 0x000-0x100 are always
preserved (untranslated latin/katakana still renders from them). Freeing an
unused Hangul slot is safe: only already-broken text could point at it.

Assets: 10 pointer tables, ui_master, music/demo pool (repack + re-aim),
        map-label heap (byte-exact in place at SECOND offset + 0x2dc).
"""
import json, struct, sys, hashlib
sys.path.insert(0, "D:/ps1/roms/SRWCB/korean_patch/tools")
from second_translation_codec import (load_safe_glyph_map, required_extra_characters,
                                      add_extra_glyph_mapping, normalise_for_font)
from build_second_expanded_patch import FONT_EXE_LAYOUT

ROOT = "D:/ps1/roms/SRWCB/korean_patch"
SP = "C:/Users/Jay/AppData/Local/Temp/claude/D--ps1-roms-SRWCB/57133a9b-927c-4883-b4d5-bbcc7cdad986/scratchpad"
EXTRA_GLYPH_START, GLYPH_COUNT, GLYPH_BYTES = 0xA2F, 0xB00, 32
STRUCT_GLYPHS = {0x3FF, 0x6FF, 0x700}
CTRL_ARGS = {0xF6:0,0xF7:0,0xF8:1,0xF9:1,0xFA:0,0xFB:2,0xFC:2,0xFD:2,0xFE:1}
LABEL_DELTA = 0x2dc

SRC = f"{ROOT}/test_build/third_full/runtime/THIRD/THIRD.WAR"
war = bytearray(open(SRC, "rb").read()); N = len(war)
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
for tb in json.load(open(f"{ROOT}/translation_v2/second_ui_tables_overlay.json",encoding="utf-8"))["tables"]:
    for e in tb["entries"]:
        if e.get("source_text") and e.get("korean_text") and str(e["korean_text"]).strip(): jp2ko[e["source_text"]]=e["korean_text"]
for tb in json.load(open(f"{ROOT}/translation_v2/second_ui_names_overlay.json",encoding="utf-8"))["tables"]:
    for r in tb["rows"]:
        if r.get("japanese") and r.get("korean") and str(r["korean"]).strip(): jp2ko[r["japanese"]]=r["korean"]
newtr={k:v for k,v in json.load(open(f"{SP}/third_ui_translations.json",encoding="utf-8")).items() if not k.startswith("_")}
for k,v in newtr.items():
    if v: jp2ko.setdefault(k,v)
span_map={}
for a in json.load(open(f"{ROOT}/translation_v2/second_ui_scripts_overlay.json",encoding="utf-8"))["assets"].values():
    for r in a["records"]:
        for rep in r.get("replacements",[]):
            if rep.get("korean_text"): span_map[bytes.fromhex(rep["source_hex"].replace(" ",""))]=rep["korean_text"]
for jp,ko in jp2ko.items():
    b=enc_jp(jp)
    if b and len(b)>=2: span_map.setdefault(b,ko)
maxspan=max(len(b) for b in span_map)
labels=json.load(open(f"{ROOT}/translation_v2/second_ui_map_labels_overlay.json",encoding="utf-8"))["records"]

# ---------------- final glyph map + donor slots ----------------
ko_all=list(jp2ko.values())+list(span_map.values())+[x["korean_text"] for x in labels if x.get("korean_text")]
ko_all+=[v for t in json.load(open(f"{ROOT}/translation_v2/third_translation_overlay.json",encoding="utf-8"))["translations"].values() for v in t["ko_parts"].values()]
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
    """Encode Korean UI text. [Fx] markers become their control byte."""
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
font_off=next(v for k,v in FONT_EXE_LAYOUT.items() if str(k).replace("\\","/").endswith("THIRD.WAR"))
free=[i for i in range(0x101,GLYPH_COUNT) if i not in keep]
runs=[];s=p=None
for i in free:
    if s is None: s=p=i; continue
    if i==p+1: p=i
    else: runs.append([font_off+s*GLYPH_BYTES,font_off+(p+1)*GLYPH_BYTES]); s=p=i
if s is not None: runs.append([font_off+s*GLYPH_BYTES,font_off+(p+1)*GLYPH_BYTES])
runs.sort(key=lambda r:-(r[1]-r[0]))
ARENA=runs; arena_total=sum(b-a for a,b in ARENA)
print(f"donor blocks: {len(ARENA)}  total {arena_total} bytes")
arena_used=0
def arena_alloc(n):
    global arena_used
    for blk in ARENA:
        if blk[1]-blk[0]>=n:
            off=blk[0]; blk[0]+=n; arena_used+=n; return off
    raise SystemExit(f"ARENA OVERFLOW need {n}")

def rebuild_record(start):
    end=rec_end(war,start); out=bytearray(); p=start; hit=0
    idx={t[0]:t for t in tokens(war,start)}
    while p<end-1:
        t=idx.get(p)
        if t is None: out.append(war[p]); p+=1; continue
        s_,n_,k_=t
        if k_=='g':
            best=None
            for L in range(min(maxspan,end-1-p),1,-1):
                c=bytes(war[p:p+L])
                if c in span_map: best=(L,span_map[c]); break
            if best: out+=enc_ko(best[1]); p+=best[0]; hit+=1; continue
        out+=war[s_:s_+n_]; p=s_+n_
    out.append(0xFF); return bytes(out),hit

manifest=[]
def repack(name,entries,pool_lo,pool_hi,pf):
    newpos={}; cur=pool_lo; ov=0
    for t in sorted(entries):
        b=entries[t]
        if cur+len(b)<=pool_hi: newpos[t]=cur; cur+=len(b)
        else: newpos[t]=arena_alloc(len(b)); ov+=len(b)
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
for name,ptr,cnt,bound in TABLES:
    pool_lo=ptr+4+4*cnt; recs={}; pf=[]
    for k in range(cnt):
        f=ptr+4+4*k; t=f+s32(f); pf.append((f,t))
        if not (pool_lo<=t and rec_end(war,t)<=bound) or t in recs: continue
        jp=decode(war,t); ko=jp2ko.get(jp)
        recs[t]=(enc_ko(ko)+b"\xFF") if ko else bytes(war[t:rec_end(war,t)])
    repack(name,recs,pool_lo,min(max(rec_end(war,t) for t in recs),bound),pf)

MH,MC=0x247CC,107
pool_lo=MH+4+4*MC; recs={}; pf=[]; hits=0
for k in range(MC):
    f=MH+4+4*k; t=f+s32(f); pf.append((f,t))
    if not (0x800<=t<N) or t in recs: continue
    b,h=rebuild_record(t); recs[t]=b; hits+=h
print(f"  ui_master span hits: {hits}")
repack("ui_master",recs,pool_lo,max(rec_end(war,t) for t in recs),pf)

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
    if war[o:o+len(src)]!=src or not ko or not str(ko).strip(): skip+=1; continue
    e=enc_ko(ko)
    if len(e)>len(src): toolong.append((x["japanese_text"],ko,len(src),len(e))); grew+=1; continue
    war[o:o+len(src)]=e+b"\x00"*(len(src)-len(e)); ok+=1
print(f"  map_labels           in-place={ok} too_long={grew} skip={skip}")
for t in toolong[:8]: print("     TOO LONG:",t)

out=f"{ROOT}/test_build/third_full/runtime/THIRD/THIRD.WAR"
open(out,"wb").write(bytes(war))
print(f"\ndonor used {arena_used}/{arena_total}")
json.dump({"extras":EXTRAS,"donor_used":arena_used,"assets":manifest,"labels_ok":ok},
          open(f"{SP}/third_ui_inject_manifest.json","w",encoding="utf-8"),ensure_ascii=False,indent=1)
print("WROTE",out,"sha",hashlib.sha256(bytes(war)).hexdigest()[:16])
