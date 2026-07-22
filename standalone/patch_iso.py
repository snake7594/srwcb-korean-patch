"""Apply the ported Korean patch to the SRW2 CloneCD image.

 * SLPS_024.06 is the same size -> overwrite its existing sectors in place.
 * BMESS2 / 2_DEAD / 2_SCE grew -> place them in the trailing NULL.DA free
   region (all-zero MODE0 track-2 padding at LBA 232670..246170) and retarget
   their ISO directory entries.  Image size / .ccd / .sub are unchanged.

All written sectors are valid MODE2 Form1 (sync + MSF header + submode + user
data + EDC/ECC).  Directory records are re-checksummed.
"""
import struct, math, shutil, hashlib, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config
sys.path.insert(0, str(config.TOOLS))
from patch_raw_track_exes import SECTOR_SIZE, USER_DATA_OFFSET, USER_DATA_SIZE, rebuild_mode2_form1

SRC_IMG=str(config.SRW2_IMG)
OUT_IMG=str(config.OUT_IMG)
CBR=str(config.CB_REBUILT)+"/"
EX=str(config.SRW2_EXTRACTED)+"/"

# (iso_name, retail_source, patched_file)
INPLACE=[("SLPS_024.06;1", EX+"SLPS_024.06", str(config.PATCHED_EXE))]
RELOC=[(iso, EX+src, CBR+pat) for iso,src,pat in config.RELOC_FILES]
FREE_START=config.NULL_DA_FREE_START
FREE_END=config.NULL_DA_FREE_END

def bcd(v): return ((v//10)<<4)|(v%10)
def sector_header(lba):
    ab=lba+150; m,r=divmod(ab,75*60); s,f=divmod(r,75)
    return bytes((bcd(m),bcd(s),bcd(f),2))
def make_sector(lba,payload,final):
    sec=bytearray(SECTOR_SIZE)
    sec[0:12]=b"\x00"+b"\xFF"*10+b"\x00"
    sec[12:16]=sector_header(lba)
    sub=0x89 if final else 0x08
    sec[16:20]=bytes((0,0,sub,0)); sec[20:24]=sec[16:20]
    sec[USER_DATA_OFFSET:USER_DATA_OFFSET+len(payload)]=payload
    rebuild_mode2_form1(sec)
    return bytes(sec)

def find_dir_entry(track, iso_name, limit):
    name=iso_name.encode("ascii")
    track.seek(0); data=track.read(limit)
    at=data.find(name)
    while at>=0:
        start=at-33
        if start>=0 and data[start]>=33+len(name) and data[start+32]==len(name) and data[start+33:start+33+len(name)]==name:
            return start
        at=data.find(name,at+1)
    raise ValueError("dir entry not found: "+iso_name)

def write_file_at(track, start_lba, payload):
    count=math.ceil(len(payload)/USER_DATA_SIZE)
    for i in range(count):
        chunk=payload[i*USER_DATA_SIZE:(i+1)*USER_DATA_SIZE]
        track.seek((start_lba+i)*SECTOR_SIZE)
        track.write(make_sector(start_lba+i,chunk,i==count-1))
    return count

def retarget(track, iso_name, new_lba, new_size, exp_old_size, limit):
    ent=find_dir_entry(track, iso_name, limit)
    slba,off=divmod(ent,SECTOR_SIZE)
    track.seek(slba*SECTOR_SIZE); sec=bytearray(track.read(SECTOR_SIZE))
    old_lba=struct.unpack_from("<I",sec,off+2)[0]; old_size=struct.unpack_from("<I",sec,off+10)[0]
    assert old_size==exp_old_size, f"{iso_name}: dir old_size {old_size} != retail {exp_old_size}"
    struct.pack_into("<I",sec,off+2,new_lba); struct.pack_into(">I",sec,off+6,new_lba)
    struct.pack_into("<I",sec,off+10,new_size); struct.pack_into(">I",sec,off+14,new_size)
    rebuild_mode2_form1(sec)
    track.seek(slba*SECTOR_SIZE); track.write(sec)
    return old_lba,old_size

shutil.copyfile(SRC_IMG,OUT_IMG)
manifest=[]
with open(OUT_IMG,"r+b") as track:
    track.seek(0,2); phys=track.tell()//SECTOR_SIZE
    limit=FREE_START*SECTOR_SIZE
    # in-place (same size): overwrite existing sectors, dir entry unchanged
    for iso,srcp,patp in INPLACE:
        src=open(srcp,"rb").read(); pat=open(patp,"rb").read()
        assert len(src)==len(pat), f"{iso} size changed {len(src)}->{len(pat)}"
        ent=find_dir_entry(track,iso,limit); slba,off=divmod(ent,SECTOR_SIZE)
        track.seek(slba*SECTOR_SIZE); s=track.read(SECTOR_SIZE)
        old_lba=struct.unpack_from("<I",s,off+2)[0]
        cnt=write_file_at(track,old_lba,pat)
        manifest.append(dict(iso=iso,mode="in-place",lba=old_lba,size=len(pat),sectors=cnt))
    # relocated (grown): place in NULL.DA region
    next_lba=FREE_START
    for iso,srcp,patp in RELOC:
        src=open(srcp,"rb").read(); pat=open(patp,"rb").read()
        count=math.ceil(len(pat)/USER_DATA_SIZE)
        assert next_lba+count<=FREE_END, f"{iso}: overflow free region"
        write_file_at(track,next_lba,pat)
        old=retarget(track,iso,next_lba,len(pat),len(src),limit)
        manifest.append(dict(iso=iso,mode="reloc",old_lba=old[0],new_lba=next_lba,size=len(pat),sectors=count))
        next_lba+=count

# Single MODE2/2352 data-track cue: the relocated files land in the disc's
# trailing null region which the original .ccd marks as an AUDIO track (track 2);
# reading them as data there fails.  Treating the whole image as one data track
# makes them read cleanly.  (User-confirmed: .cue works, .ccd freezes.)
cue_name=config.OUT_CUE.name
img_name=config.OUT_IMG.name
config.OUT_CUE.write_text(
    f'FILE "{img_name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n'
)

print("OUT:",OUT_IMG)
for m in manifest: print("  ",m)
print("free region used:",next_lba-FREE_START,"of",FREE_END-FREE_START,"sectors")
print("out sha256:",hashlib.sha256(open(OUT_IMG,'rb').read()).hexdigest()[:16])
print("cue:",config.OUT_CUE)
