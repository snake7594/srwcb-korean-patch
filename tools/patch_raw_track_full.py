#!/usr/bin/env python3
"""Apply fixed-size extracted-file edits to a MODE2/2352 CD track."""
from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path

from patch_raw_track_exes import SECTOR_SIZE, USER_DATA_OFFSET, USER_DATA_SIZE, rebuild_mode2_form1

LAYOUT = {
    Path("SLPS_020.70"): 239346,
    Path("BMESS2.BIN"): 1133,
    Path("SECOND/2_DEAD.BIN"): 24715,
    Path("SECOND/2_SCE.BIN"): 24718,
    Path("SECOND/SECOND.WAR"): 24922,
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024): h.update(chunk)
    return h.hexdigest()


def patch_one(track, lba: int, source: bytes, patched: bytes, label: str) -> int:
    if len(source) != len(patched): raise ValueError(f"{label}: size changed")
    sectors = (len(source) + USER_DATA_SIZE - 1) // USER_DATA_SIZE
    changed = 0
    for i in range(sectors):
        track.seek((lba + i) * SECTOR_SIZE)
        sector = bytearray(track.read(SECTOR_SIZE))
        if len(sector) != SECTOR_SIZE: raise ValueError(f"{label}: truncated sector {i}")
        begin = i * USER_DATA_SIZE; end = min(begin + USER_DATA_SIZE, len(source))
        old = bytes(sector[USER_DATA_OFFSET:USER_DATA_OFFSET + end - begin])
        if old != source[begin:end]: raise ValueError(f"{label}: raw source mismatch at sector {i}")
        replacement = patched[begin:end]
        if replacement == old: continue
        sector[USER_DATA_OFFSET:USER_DATA_OFFSET + len(replacement)] = replacement
        rebuild_mode2_form1(sector)
        track.seek((lba + i) * SECTOR_SIZE); track.write(sector); changed += 1
    return changed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source_track", type=Path)
    ap.add_argument("output_track", type=Path)
    ap.add_argument("source_extracted", type=Path)
    ap.add_argument("patched_extracted", type=Path)
    args = ap.parse_args()
    if args.source_track.resolve() == args.output_track.resolve(): raise SystemExit("output must differ from source")
    args.output_track.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(args.source_track, args.output_track)
    total = 0
    with args.output_track.open("r+b") as track:
        for rel, lba in LAYOUT.items():
            old = (args.source_extracted / rel).read_bytes(); new = (args.patched_extracted / rel).read_bytes()
            changed = patch_one(track, lba, old, new, str(rel)); total += changed
            print(f"{rel}: LBA {lba}, bytes {len(old)}, changed sectors {changed}, sha256 {hashlib.sha256(new).hexdigest()}")
    print(f"changed sectors: {total}")
    print(f"source track sha256: {sha256_file(args.source_track)}")
    print(f"patched track sha256: {sha256_file(args.output_track)}")
    print(f"patched track: {args.output_track.resolve()}")
    return 0


if __name__ == "__main__": raise SystemExit(main())
