"""Port the v0.8.7 Korean executable patch (SECOND.WAR) -> SLPS_024.06.

Generic delta-transplant. For every changed 4-aligned word we classify by the
RETAIL relationship (patched alone is ambiguous), using a consistency test that
retail-SLP must equal retail-SEC ported by the delta map:

  ABSOLUTE ptr (KSEG0 0x80.. or KUSEG 0x00.. into the image/BSS):
      retail-SLP == retail-SEC + delta(target)   -> re-aim  new = patched + delta(patched_target)
  SELF-RELATIVE ptr (field + s32(val) lands in image):
      retail-SLP == retail-SEC + delta(rtarget) - delta(field)
                                                  -> new = patched + delta(ptarget) - delta(field)
  else verbatim.

Music-pool pointer fields are 1-byte-unaligned, handled explicitly (same self-rel formula).
"""
import struct, json, bisect, hashlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

rs=open(config.CB_RETAIL_SECOND,"rb").read()
ps=open(config.CB_PATCHED_SECOND,"rb").read()
sr=open(config.SRW2_RETAIL_EXE,"rb").read()
zones=json.load(open(config.DELTA_MAP)); zstarts=[z[0] for z in zones]
music_fields=sorted(json.load(open(config.MUSIC_FIELDS)))
FZ=zstarts[0]; SEC_LEN=len(rs); SLPS_LEN=len(sr); LAST_DELTA=zones[-1][2]

def delta(o):
    if 0x800<=o<FZ: return 0
    i=bisect.bisect_right(zstarts,o)-1
    if i<0: return None
    zs,ze,zd=zones[i]; return zd if zs<=o<ze else None
def tdelta(tf): return LAST_DELTA if tf>=SEC_LEN else delta(tf)
def u32(b,o): return struct.unpack_from("<I",b,o)[0]
def s32(b,o): return struct.unpack_from("<i",b,o)[0]
def abs_tf(v):
    if (v>>28) not in (0x8,0x0): return None
    tf=(v&0x1FFFFFFF)-0xF800
    return tf if 0x800<=tf<SEC_LEN+0x40000 else None
def rel_tf(field,v):
    t=field+((v^0x80000000)-0x80000000 if v>=0x80000000 else v)  # s32
    return t if 0x800<=t<SEC_LEN else None
M32=0xffffffff

out=bytearray(sr)
st=dict(absptr=0,selfrel=0,music=0,verbatim=0,skip=0); prob=[]
music_set=set(music_fields)

# --- pass A: music-pool self-relative pointers (1-byte-unaligned) ---
for f in music_fields:
    d=delta(f)
    if d is None: prob.append(("music-no-delta",hex(f))); continue
    pv=u32(ps,f); pst=rel_tf(f,pv)
    if pst is None: prob.append(("music-tgt",hex(f))); continue
    struct.pack_into("<I",out,f+d,(pv+tdelta(pst)-d)&M32); st["music"]+=1

def overlaps_music(a):
    return any(mf<=a+3 and mf+4>a for mf in music_set)

# --- pass B: generic aligned pointer classification for all other changed words ---
i=0x800
while i<SEC_LEN:
    if rs[i]==ps[i]: i+=1; continue
    a=i&~3
    if overlaps_music(a): st["skip"]+=1; i=a+4; continue
    d=delta(a)
    if d is None:
        db=delta(i)
        if db is None: prob.append(("no-delta",hex(i))); i+=1; continue
        out[i+db]=ps[i]; st["verbatim"]+=1; i+=1; continue
    # relocated-text / font-glyph region: pure text, no outgoing pointers -> verbatim.
    # (text bytes can coincidentally look like self-rel pointers; never re-aim here.)
    if 0x2A07A<=a<0x3E058:
        for k in range(4):
            if a+k<SEC_LEN and rs[a+k]!=ps[a+k] and 0<=a+d+k<SLPS_LEN: out[a+d+k]=ps[a+k]; st["verbatim"]+=1
        i=a+4; continue
    rv,pv=u32(rs,a),u32(ps,a); slp=u32(sr,a+d)
    handled=False
    # ABSOLUTE
    rt,pt,slt=abs_tf(rv),abs_tf(pv),abs_tf(slp)
    if rt is not None and slt is not None and pt is not None and slp==(rv+tdelta(rt))&M32:
        struct.pack_into("<I",out,a+d,(pv+tdelta(pt))&M32); st["absptr"]+=1; handled=True
    if not handled:
        # SELF-RELATIVE
        rrt=rel_tf(a,rv); prt=rel_tf(a,pv); srt=rel_tf(a+d,slp)
        if rrt is not None and srt is not None and prt is not None and slp==(rv+tdelta(rrt)-d)&M32:
            struct.pack_into("<I",out,a+d,(pv+tdelta(prt)-d)&M32); st["selfrel"]+=1; handled=True
    if not handled:
        for k in range(4):
            if a+k<SEC_LEN and rs[a+k]!=ps[a+k]:
                if 0<=a+d+k<SLPS_LEN: out[a+d+k]=ps[a+k]; st["verbatim"]+=1
    i=a+4

struct.pack_into("<I",out,0x1c,u32(sr,0x1c)+(u32(ps,0x1c)-u32(rs,0x1c)))

# --- fix split lui/ori & lui/addiu address loads the generic word pass can't see ---
# battle_scratch: heap/BSS/scratch base addresses (loaded across two instructions).
# SLP address = SECOND-patched address + module-end delta (0xd70).
def fix_split_addr(lui_off, imm_off, is_ori):
    dL=delta(lui_off); dI=delta(imm_off)
    lw=u32(ps,lui_off); iw=u32(ps,imm_off)
    lui_imm=lw & 0xffff; imm=iw & 0xffff
    if is_ori: addr=(lui_imm<<16)|imm
    else:      addr=(lui_imm<<16)+((imm^0x8000)-0x8000)      # signed
    addr=(addr+LAST_DELTA)&0xffffffff
    if is_ori: nlui=(addr>>16)&0xffff; nimm=addr&0xffff
    else:      nlui=((addr+0x8000)>>16)&0xffff; nimm=addr&0xffff
    struct.pack_into("<I",out,lui_off+dL,(lw & 0xffff0000)|nlui)
    struct.pack_into("<I",out,imm_off+dI,(iw & 0xffff0000)|nimm)
    st["split_addr"]=st.get("split_addr",0)+1
    return hex(addr)
scratch_fixes={
 "bss_clear_end": fix_split_addr(0x44354,0x44358,False),
 "initheap_base": fix_split_addr(0x4439C,0x443A0,False),
 "scratch_base":  fix_split_addr(0xC3020,0xC3024,True),
}
print("scratch split-addr SLP targets:",scratch_fixes)
print("stats:",st)
if prob: print("PROBLEMS:",prob[:20])
assert len(out)==SLPS_LEN
config.OUT_DIR.mkdir(parents=True, exist_ok=True)
open(config.PATCHED_EXE,"wb").write(out)
print("wrote",config.PATCHED_EXE.name,hex(len(out)),"sha256",hashlib.sha256(out).hexdigest()[:16])
