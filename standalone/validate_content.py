"""Definitive content validation: follow every self-relative UI-table pointer in
the ported SLPS and confirm each resolves to a record byte-identical to the
corresponding record in the proven patched SECOND.WAR.  Also decode a sample to
Korean.  If every table's resolved record set matches, the port is correct.
"""
import struct, json, bisect, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

P="D:/ps1/roms/"  # retained only for the two records_equiv paths below
ps=open(config.CB_PATCHED_SECOND,"rb").read()
sp=open(config.PATCHED_EXE,"rb").read()
zones=json.load(open(config.DELTA_MAP)); zstarts=[z[0] for z in zones]
FZ=zstarts[0]
def delta(o):
    if 0x800<=o<FZ: return 0
    i=bisect.bisect_right(zstarts,o)-1
    if i<0: return None
    zs,ze,zd=zones[i]; return zd if zs<=o<ze else None
mp=json.load(open(config.CB_ROOT/"research"/"srwcb_embedded_font_mapping_reviewed.json",encoding="utf-8"))
idx2ch={r["glyph_index"]:(r.get("character") or "") for r in mp["rows"]}
def decode(b):
    o=[];i=0
    while i<len(b):
        x=b[i]
        if x==0xFF: break
        if x>=0xF6: o.append(f"[{x:02X}]"); i+=1
        elif 0xEB<=x<=0xF5: o.append(idx2ch.get((x-0xEB)*0x100+b[i+1]) or f"<{(x-0xEB)*0x100+b[i+1]:03X}>"); i+=2
        else: o.append(idx2ch.get(x) or f"<{x:02X}>"); i+=1
    return "".join(o)

# self-relative pointer tables: (name, sec_header_off, count)
TABLES=[
 ("scenario_titles",0x113C4,192),
 ("pilot_short_names",0x10CE0C,400),
 ("pilot_full_names",0x10DD64,400),
 ("unit_names",0x10F478,448),
 ("terrain_names",0xB830,144),
 ("spirit_commands",0xBC74,94),
 ("enhancement_parts",0xC340,64),
 ("weapon_names",0xC6B8,1408),
 ("pilot_skills",0x11020,52),
 ("unit_abilities",0x112B8,22),
]
def read_table(buf,base_hdr,count,off_delta):
    """header at base_hdr(+delta). Each entry is s32 self-relative from its field."""
    hdr=base_hdr+off_delta
    start=hdr+4
    recs=[]
    for k in range(count):
        field=start+4*k
        rel=struct.unpack_from("<i",buf,field)[0]
        tgt=field+rel
        end=buf.find(b"\xff",tgt,tgt+400)
        recs.append(buf[tgt:end+1] if end>=0 else buf[tgt:tgt+8])
    return recs

BIAS=0x8000F800; SEC_LEN=len(ps); LAST=zones[-1][2]
def tdelta(tf): return LAST if tf>=SEC_LEN else delta(tf)
def records_equiv_at(sa,sb,base_a,base_b):
    """Field-aware: allow embedded ABSOLUTE (KSEG0/KUSEG) or SELF-RELATIVE pointers
    to differ, provided SLP resolves to the SEC target shifted by the delta map."""
    if sa==sb: return True
    if len(sa)!=len(sb): return False
    j=0
    while j<len(sa):
        if sa[j]==sb[j]: j+=1; continue
        matched=False
        for base in range(max(0,j-3), j+1):
            if base+4<=len(sa):
                va=int.from_bytes(sa[base:base+4],'little'); vb=int.from_bytes(sb[base:base+4],'little')
                # absolute
                if (va>>28) in (0x8,0x0):
                    tf=(va&0x1FFFFFFF)-0xF800
                    if 0x800<=tf<SEC_LEN+0x40000:
                        td=tdelta(tf)
                        if td is not None and vb==(va+td)&0xffffffff: j=base+4; matched=True; break
                # self-relative
                sva=(va^0x80000000)-0x80000000 if va>=0x80000000 else va
                fa=base_a+base; ta=fa+sva
                if 0x800<=ta<SEC_LEN:
                    td=tdelta(ta); fb=base_b+base
                    if td is not None and vb==((va+td-(base_b-base_a))&0xffffffff): j=base+4; matched=True; break
        if not matched: return False
    return True
def records_equiv(sa,sb):
    """Equal, allowing embedded absolute RAM pointers to differ by their correct
    target delta (retail SLPS keeps its own valid pointer inside shared records)."""
    if sa==sb: return True
    if len(sa)!=len(sb): return False
    j=0
    while j<len(sa):
        if sa[j]==sb[j]: j+=1; continue
        # check a 4-byte little-endian abs pointer starting at j-3..j
        matched=False
        for base in range(max(0,j-3), j+1):
            if base+4<=len(sa):
                va=int.from_bytes(sa[base:base+4],'little')
                vb=int.from_bytes(sb[base:base+4],'little')
                seg=va>>28
                if seg in (0x8,0x0):
                    phys=va&0x1FFFFFFF; tf=phys-0xF800
                    if 0x800<=tf<len(ps): td=delta(tf)
                    elif len(ps)<=tf<len(ps)+0x40000: td=zones[-1][2]
                    else: td=None
                    if td is not None and vb==(va+td)&0xffffffff:
                        j=base+4; matched=True; break
        if not matched: return False
    return True

allok=True; sample=[]
for name,hdr,count in TABLES:
    d=delta(hdr)
    if d is None:
        print(f"{name}: header delta None -> SKIP"); allok=False; continue
    sec=read_table(ps,hdr,count,0)
    slp=read_table(sp,hdr,count,d)
    # target offsets for field-aware self-rel pointer checking inside records
    def tgt(buf,base_hdr,count,od,k):
        field=base_hdr+od+4+4*k; return field+struct.unpack_from("<i",buf,field)[0]
    mism=[k for k in range(count) if not records_equiv_at(sec[k],slp[k],tgt(ps,hdr,count,0,k),tgt(sp,hdr,count,d,k))]
    tag="OK" if not mism else f"MISMATCH {len(mism)} (e.g. idx {mism[:5]})"
    print(f"{name:20s} count={count:>4}  {tag}")
    if mism: allok=False
    if name=="scenario_titles":
        for k in (0,1,2,3):
            sample.append((k, decode(slp[k]), decode(sec[k])))

# music pool: 172 self-relative fields -> records; compare resolved bytes
music=sorted(json.load(open(config.MUSIC_FIELDS)))
mmis=0
for f in music:
    d=delta(f)
    rel_sec=struct.unpack_from("<i",ps,f)[0]; tsec=f+rel_sec
    rel_slp=struct.unpack_from("<i",sp,f+d)[0]; tslp=f+d+rel_slp
    esec=ps.find(b"\xff",tsec,tsec+400); eslp=sp.find(b"\xff",tslp,tslp+400)
    if not records_equiv(ps[tsec:esec+1],sp[tslp:eslp+1]): mmis+=1
print(f"music_pool           count= 172  {'OK' if mmis==0 else f'MISMATCH {mmis}'}")
if mmis: allok=False

print("\nscenario_titles sample (SLPS decoded | SECOND decoded):")
for k,a,b in sample: print(f"  [{k}] {a!r}   |   {b!r}")
print("\n=== PORT CONTENT VALIDATION:", "ALL TABLES MATCH ✓" if allok else "FAILURES ✗", "===")
