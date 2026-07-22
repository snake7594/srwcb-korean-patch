"""Extract the retail files the port needs from the SRW2 CloneCD image, and
derive music_pointer_fields.json from the Complete Box inventory.

Outputs into config.SRW2_EXTRACTED and config.MUSIC_FIELDS.
"""
import struct, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import config

SEC = 2352

def _user(f, lba):
    f.seek(lba * SEC + 24); return f.read(2048)

def _read_file(f, lba, size):
    n = (size + 2047) // 2048
    return b"".join(_user(f, lba + i) for i in range(n))[:size]

def _read_dir(f, lba, size):
    data = b"".join(_user(f, lba + i) for i in range((size + 2047) // 2048))
    out = []; i = 0
    while i < len(data):
        L = data[i]
        if L == 0:
            i = (i // 2048 + 1) * 2048; continue
        rec = data[i:i + L]
        out.append((rec[33:33 + rec[32]].decode('ascii', 'replace'),
                    struct.unpack_from("<I", rec, 2)[0],
                    struct.unpack_from("<I", rec, 10)[0], rec[25]))
        i += L
    return out

def extract():
    config.SRW2_EXTRACTED.mkdir(parents=True, exist_ok=True)
    (config.SRW2_EXTRACTED / "SECOND").mkdir(exist_ok=True)
    with open(config.SRW2_IMG, "rb") as f:
        pvd = _user(f, 16)
        root_lba = struct.unpack_from("<I", pvd[156:], 2)[0]
        root = _read_dir(f, root_lba, 2048)
        want_root = {"SLPS_024.06", "BMESS2.BIN"}
        second_lba = None
        for name, lba, size, flags in root:
            base = name[:-2] if name.endswith(";1") else name
            if flags & 2 and base == "SECOND":
                second_lba = lba
            if base in want_root:
                (config.SRW2_EXTRACTED / base).write_bytes(_read_file(f, lba, size))
                print("extracted", base, size)
        assert second_lba, "SECOND directory not found"
        for name, lba, size, flags in _read_dir(f, second_lba, 2048):
            base = name[:-2] if name.endswith(";1") else name
            if base in {"2_SCE.BIN", "2_DEAD.BIN"}:
                (config.SRW2_EXTRACTED / "SECOND" / base).write_bytes(_read_file(f, lba, size))
                print("extracted SECOND/" + base, size)

def derive_music_fields():
    inv = json.load(open(config.CB_INVENTORY, encoding="utf-8"))
    pf = []
    for r in inv["common_music_demo_pool"]["records"]:
        pf.extend(r.get("pointer_fields", []))
    config.OUT_DIR.mkdir(parents=True, exist_ok=True)
    json.dump(sorted(pf), open(config.MUSIC_FIELDS, "w"))
    print("music_pointer_fields:", len(pf))

if __name__ == "__main__":
    extract()
    derive_music_fields()
