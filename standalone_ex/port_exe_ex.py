"""Port the dialogue-only EX.WAR patch -> standalone SLPS_025.29 (SRW EX).

Reuses the proven SRW2/SRW3 delta-transplant classifier. EX.WAR changes are:
  word@0x800 (BSS-end ptr, absolute), font [0x2872c,0x3e72c) verbatim,
  embedded BMESS4 table [0x10431c,0x10495c) verbatim.
EX needs no battle-scratch relocation (0x79 <= 0x200).
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
import struct, json, bisect, hashlib
ROOT=str(_P.WORK)
rs=open(f"{ROOT}/extracted/EX/EX.WAR","rb").read()               # retail EX.WAR
ps=open(f"{ROOT}/test_build/ex_full/runtime/EX/EX.WAR","rb").read()  # patched
sr=open(str(_P.WORK / "srwex" / "extracted") + "/SLPS_025.29","rb").read()          # retail standalone
zones=json.load(open(str(_P.WORK / "srwex" / "extracted") + "/delta_map_ex.json")); zstarts=[z[0] for z in zones]
FZ=zstarts[0]; SEC_LEN=len(rs); SLPS_LEN=len(sr); LAST_DELTA=zones[-1][2]
VERBATIM=[(0x1d544,0x33544),(0x10431c,0x10495c)]   # font(EX 0x1d544), embedded BMESS4 table(EX 0x10431c)
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

# EX는 배틀 스크래치 사용량이 0x79로 한계(0x200) 내라 BSS 재배치 패치가 불필요하다.
scratch={}

print("LAST_DELTA:",hex(LAST_DELTA))
print("scratch split-addr SLP targets:",scratch)
print("stats:",st)
if prob: print("PROBLEMS:",prob[:20])
assert len(out)==SLPS_LEN
open(str(_P.WORK / "srwex" / "extracted") + "/SLPS_025.29.patched","wb").write(out)
print("wrote SLPS_025.29.patched",hex(len(out)),"sha256",hashlib.sha256(out).hexdigest()[:16])
