"""Patch the SRW3 standalone image with the ported Korean SLPS_025.30 + relocated data files.

 * SLPS_025.30 (same size) -> overwrite in place.
 * 3_SCE / BMESS3 / 3_DEAD grew -> place in the trailing NULL.DA free region
   (LBA 232755..) and retarget their ISO directory entries.
 * Single MODE2/2352 data-track .cue (the relocated files land in the disc's
   trailing null / audio-track region; treating the whole image as one data
   track makes them read cleanly — proven on the SRW2 standalone).
"""
import struct, math, shutil, hashlib, sys
sys.path.insert(0,"D:/ps1/roms/SRWCB/korean_patch/tools")
from patch_raw_track_exes import SECTOR_SIZE, USER_DATA_OFFSET, USER_DATA_SIZE, rebuild_mode2_form1

SRW3="D:/ps1/roms/SRW3"
CBR="D:/ps1/roms/SRWCB/korean_patch/test_build/third_korean/rebuilt"
SRC_IMG=f"{SRW3}/Dai 3 Ji Super Robot Taisen.bin"
OUT_DIR=f"{SRW3}/port"
import os; os.makedirs(OUT_DIR,exist_ok=True)
OUT_IMG=f"{OUT_DIR}/Dai 3 Ji Super Robot Taisen (Korean).bin"
OUT_CUE=f"{OUT_DIR}/Dai 3 Ji Super Robot Taisen (Korean).cue"
PATCHED_EXE=f"{SRW3}/extracted/SLPS_025.30.patched"
RETAIL_EXE=f"{SRW3}/extracted/SLPS_025.30"

# (iso_name, retail_size, patched_path)   -- retail_size = standalone dir old_size for the assert
INPLACE=[("SLPS_025.30;1", RETAIL_EXE, PATCHED_EXE)]
RELOC=[("3_SCE.BIN;1", 684344, f"{CBR}/THIRD/3_SCE.BIN"),
       ("BMESS3.BIN;1", 582354, f"{CBR}/BMESS3.BIN"),
       ("3_DEAD.BIN;1", 5754,   f"{CBR}/THIRD/3_DEAD.BIN")]
FREE_START=232755; FREE_END=246105

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
    rebuild_mode2_form1(sec); return bytes(sec)
def find_dir_entry(track, iso_name, limit):
    name=iso_name.encode("ascii"); track.seek(0); data=track.read(limit); at=data.find(name)
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
        track.seek((start_lba+i)*SECTOR_SIZE); track.write(make_sector(start_lba+i,chunk,i==count-1))
    return count
def retarget(track, iso_name, new_lba, new_size, exp_old_size, limit):
    ent=find_dir_entry(track, iso_name, limit); slba,off=divmod(ent,SECTOR_SIZE)
    track.seek(slba*SECTOR_SIZE); sec=bytearray(track.read(SECTOR_SIZE))
    old_lba=struct.unpack_from("<I",sec,off+2)[0]; old_size=struct.unpack_from("<I",sec,off+10)[0]
    assert old_size==exp_old_size, f"{iso_name}: dir old_size {old_size} != {exp_old_size}"
    struct.pack_into("<I",sec,off+2,new_lba); struct.pack_into(">I",sec,off+6,new_lba)
    struct.pack_into("<I",sec,off+10,new_size); struct.pack_into(">I",sec,off+14,new_size)
    rebuild_mode2_form1(sec); track.seek(slba*SECTOR_SIZE); track.write(sec)
    return old_lba,old_size

shutil.copyfile(SRC_IMG,OUT_IMG)
manifest=[]
with open(OUT_IMG,"r+b") as track:
    limit=FREE_START*SECTOR_SIZE
    for iso,srcp,patp in INPLACE:
        src=open(srcp,"rb").read(); pat=open(patp,"rb").read()
        assert len(src)==len(pat), f"{iso} size {len(src)}->{len(pat)}"
        ent=find_dir_entry(track,iso,limit); slba,off=divmod(ent,SECTOR_SIZE)
        track.seek(slba*SECTOR_SIZE); s=track.read(SECTOR_SIZE)
        old_lba=struct.unpack_from("<I",s,off+2)[0]
        cnt=write_file_at(track,old_lba,pat)
        manifest.append(dict(iso=iso,mode="in-place",lba=old_lba,size=len(pat),sectors=cnt))
    next_lba=FREE_START
    for iso,old_size,patp in RELOC:
        pat=open(patp,"rb").read(); count=math.ceil(len(pat)/USER_DATA_SIZE)
        assert next_lba+count<=FREE_END, f"{iso}: overflow free region"
        write_file_at(track,next_lba,pat)
        old=retarget(track,iso,next_lba,len(pat),old_size,limit)
        manifest.append(dict(iso=iso,mode="reloc",old_lba=old[0],new_lba=next_lba,size=len(pat),sectors=count))
        next_lba+=count

img_name=os.path.basename(OUT_IMG)
open(OUT_CUE,"w").write(f'FILE "{img_name}" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n')
print("OUT:",OUT_IMG)
for m in manifest: print("  ",m)
print("free used:",next_lba-FREE_START,"of",FREE_END-FREE_START,"sectors")
print("out sha256:",hashlib.sha256(open(OUT_IMG,'rb').read()).hexdigest())
print("cue:",OUT_CUE)
