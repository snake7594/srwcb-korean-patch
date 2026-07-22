"""Port the dialogue-only THIRD.WAR patch -> standalone SLPS_025.30 (SRW3).

Reuses the proven SRW2 delta-transplant classifier. THIRD.WAR changes are:
  word@0x800 (BSS-end ptr, absolute), font [0x2872c,0x3e72c) verbatim,
  embedded BMESS3 table [0x109fe4,0x10a624) verbatim, battle-scratch split
  address loads (fix_split_addr), stride opcode @0xC3898 verbatim.
No music-pool / UI-table changes (dialogue-only).
"""
import struct, json, bisect, hashlib
ROOT="D:/ps1/roms/SRWCB/korean_patch"
rs=open(f"{ROOT}/extracted/THIRD/THIRD.WAR","rb").read()               # retail THIRD.WAR
ps=open(f"{ROOT}/test_build/third_korean/thirdwar_runtime/THIRD/THIRD.WAR","rb").read()  # patched
sr=open("D:/ps1/roms/SRW3/extracted/SLPS_025.30","rb").read()          # retail standalone
zones=json.load(open("D:/ps1/roms/SRW3/extracted/delta_map3.json")); zstarts=[z[0] for z in zones]
FZ=zstarts[0]; SEC_LEN=len(rs); SLPS_LEN=len(sr); LAST_DELTA=zones[-1][2]
VERBATIM=[(0x2872c,0x3e72c),(0x109fe4,0x10a624)]   # font, embedded BMESS3 table
M32=0xffffffff

def delta(o):
    if 0x800<=o<FZ: return 0
    i=bisect.bisect_right(zstarts,o)-1
    if i<0: return None
    zs,ze,zd=zones[i]; return zd if zs<=o<ze else None
def tdelta(tf): return LAST_DELTA if tf>=SEC_LEN else delta(tf)
def u32(b,o): return struct.unpack_from("<I",b,o)[0]
def abs_tf(v):
    if (v>>28) not in (0x8,0x0): return None
    tf=(v&0x1FFFFFFF)-0xF800
    return tf if 0x800<=tf<SEC_LEN+0x40000 else None
def rel_tf(field,v):
    t=field+((v^0x80000000)-0x80000000 if v>=0x80000000 else v)
    return t if 0x800<=t<SEC_LEN else None
def in_verbatim(a): return any(s<=a<e for s,e in VERBATIM)

out=bytearray(sr)
st=dict(absptr=0,selfrel=0,verbatim=0,skip=0); prob=[]

i=0x800
while i<SEC_LEN:
    if rs[i]==ps[i]: i+=1; continue
    a=i&~3
    d=delta(a)
    if d is None:
        db=delta(i)
        if db is None: prob.append(("no-delta",hex(i))); i+=1; continue
        if 0<=i+db<SLPS_LEN: out[i+db]=ps[i]; st["verbatim"]+=1
        i+=1; continue
    if in_verbatim(a):
        for k in range(4):
            if a+k<SEC_LEN and rs[a+k]!=ps[a+k] and 0<=a+d+k<SLPS_LEN: out[a+d+k]=ps[a+k]; st["verbatim"]+=1
        i=a+4; continue
    rv,pv=u32(rs,a),u32(ps,a); slp=u32(sr,a+d); handled=False
    rt,pt,slt=abs_tf(rv),abs_tf(pv),abs_tf(slp)
    if rt is not None and slt is not None and pt is not None and slp==(rv+tdelta(rt))&M32:
        struct.pack_into("<I",out,a+d,(pv+tdelta(pt))&M32); st["absptr"]+=1; handled=True
    if not handled:
        rrt=rel_tf(a,rv); prt=rel_tf(a,pv); srt=rel_tf(a+d,slp)
        if rrt is not None and srt is not None and prt is not None and slp==(rv+tdelta(rrt)-d)&M32:
            struct.pack_into("<I",out,a+d,(pv+tdelta(prt)-d)&M32); st["selfrel"]+=1; handled=True
    if not handled:
        for k in range(4):
            if a+k<SEC_LEN and rs[a+k]!=ps[a+k] and 0<=a+d+k<SLPS_LEN: out[a+d+k]=ps[a+k]; st["verbatim"]+=1
    i=a+4

# t_size field (0x1c)
struct.pack_into("<I",out,0x1c,u32(sr,0x1c)+(u32(ps,0x1c)-u32(rs,0x1c)))

def fix_split_addr(lui_off,imm_off,is_ori):
    dL=delta(lui_off); dI=delta(imm_off)
    lw=u32(ps,lui_off); iw=u32(ps,imm_off)
    lui_imm=lw&0xffff; imm=iw&0xffff
    addr=((lui_imm<<16)|imm) if is_ori else ((lui_imm<<16)+((imm^0x8000)-0x8000))
    addr=(addr+LAST_DELTA)&0xffffffff
    if is_ori: nlui=(addr>>16)&0xffff; nimm=addr&0xffff
    else:      nlui=((addr+0x8000)>>16)&0xffff; nimm=addr&0xffff
    struct.pack_into("<I",out,lui_off+dL,(lw&0xffff0000)|nlui)
    struct.pack_into("<I",out,imm_off+dI,(iw&0xffff0000)|nimm)
    return hex(addr)
scratch={
 "bss_clear_end": fix_split_addr(0x44A04,0x44A08,False),
 "initheap_base": fix_split_addr(0x44A4C,0x44A50,False),
 "scratch_base":  fix_split_addr(0xC3890,0xC3894,True),
}
print("LAST_DELTA:",hex(LAST_DELTA))
print("scratch split-addr SLP targets:",scratch)
print("stats:",st)
if prob: print("PROBLEMS:",prob[:20])
assert len(out)==SLPS_LEN
open("D:/ps1/roms/SRW3/extracted/SLPS_025.30.patched","wb").write(out)
print("wrote SLPS_025.30.patched",hex(len(out)),"sha256",hashlib.sha256(out).hexdigest()[:16])
