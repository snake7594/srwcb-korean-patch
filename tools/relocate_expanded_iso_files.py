#!/usr/bin/env python3
"""Append expanded files to a MODE2/2352 track and retarget ISO directory entries."""
from __future__ import annotations

import argparse
import hashlib
import math
import shutil
import struct
from pathlib import Path

from patch_raw_track_exes import SECTOR_SIZE, USER_DATA_OFFSET, USER_DATA_SIZE, rebuild_mode2_form1


def bcd(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def sector_header(lba: int) -> bytes:
    absolute = lba + 150
    minute, rem = divmod(absolute, 75 * 60)
    second, frame = divmod(rem, 75)
    if minute > 99:
        raise ValueError("MSF minute exceeds two BCD digits")
    return bytes((bcd(minute), bcd(second), bcd(frame), 2))


def make_file_sector(lba: int, payload: bytes, final: bool) -> bytes:
    if len(payload) > USER_DATA_SIZE:
        raise ValueError("sector payload exceeds 2048 bytes")
    sector = bytearray(SECTOR_SIZE)
    sector[0:12] = b"\x00" + b"\xFF" * 10 + b"\x00"
    sector[12:16] = sector_header(lba)
    submode = 0x89 if final else 0x08  # DATA plus EOF/EOR on final sector
    sector[16:20] = bytes((0, 0, submode, 0))
    sector[20:24] = sector[16:20]
    sector[USER_DATA_OFFSET:USER_DATA_OFFSET + len(payload)] = payload
    rebuild_mode2_form1(sector)
    return bytes(sector)


def find_directory_entry(raw: bytes, iso_name: bytes) -> int:
    name_at = raw.find(iso_name)
    if name_at < 33:
        raise ValueError(f"ISO directory entry not found: {iso_name!r}")
    start = name_at - 33
    if raw[start] < 33 + len(iso_name) or raw[start + 32] != len(iso_name):
        raise ValueError(f"unexpected ISO directory record: {iso_name!r}")
    if raw[start + 33:start + 33 + len(iso_name)] != iso_name:
        raise ValueError(f"directory name verification failed: {iso_name!r}")
    return start


def find_directory_entry_stream(track, iso_name: bytes, limit: int | None = None) -> int:
    """Find and validate an ISO record without loading the raw track in RAM."""

    if limit is None:
        track.seek(0, 2)
        limit = track.tell()
    chunk_size = 1024 * 1024
    overlap = 96
    base = 0
    carry = b""
    while base < limit:
        track.seek(base)
        chunk = track.read(min(chunk_size, limit - base))
        data = carry + chunk
        data_base = base - len(carry)
        search_from = 0
        while True:
            name_at = data.find(iso_name, search_from)
            if name_at < 0:
                break
            start = name_at - 33
            if start >= 0:
                record_end = start + data[start]
                if (
                    data[start] >= 33 + len(iso_name)
                    and record_end <= len(data)
                    and data[start + 32] == len(iso_name)
                    and data[start + 33:start + 33 + len(iso_name)] == iso_name
                ):
                    return data_base + start
            search_from = name_at + 1
        carry = data[-overlap:]
        base += len(chunk)
    raise ValueError(f"ISO directory entry not found: {iso_name!r}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def relocate_files(
    base_track: Path,
    output_track: Path,
    files: list[tuple[str, Path, Path]],
) -> dict[str, object]:
    """Copy a raw track, append patched files, and retarget ISO entries.

    ``files`` contains ``(ISO_NAME, pristine_extracted, rebuilt_file)`` tuples.
    The returned manifest is the same document printed by the CLI.
    """

    if base_track.resolve() == output_track.resolve():
        raise ValueError("output track must differ from base track")
    output_track.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(base_track, output_track)

    jobs = []
    for iso_name, source_name, patched_name in files:
        source = Path(source_name).read_bytes()
        patched = Path(patched_name).read_bytes()
        jobs.append((iso_name.encode("ascii"), source, patched, str(patched_name)))

    with output_track.open("r+b") as track:
        track.seek(0, 2)
        if track.tell() % SECTOR_SIZE:
            raise ValueError("base track is not an integral number of raw sectors")
        next_lba = track.tell() // SECTOR_SIZE
        # The original PVD deliberately reserves sectors past the physical end.
        track.seek(16 * SECTOR_SIZE + USER_DATA_OFFSET + 80)
        volume_sectors = struct.unpack("<I", track.read(4))[0]
        manifest = []
        for iso_name, source, patched, patched_name in jobs:
            start_lba = next_lba
            count = math.ceil(len(patched) / USER_DATA_SIZE)
            if start_lba + count > volume_sectors:
                raise ValueError(
                    f"{iso_name!r}: appended extent ends at {start_lba + count}, "
                    f"past PVD volume size {volume_sectors}"
                )
            track.seek(0, 2)
            for i in range(count):
                chunk = patched[i * USER_DATA_SIZE:(i + 1) * USER_DATA_SIZE]
                track.write(make_file_sector(start_lba + i, chunk, i == count - 1))
            next_lba += count

            entry = find_directory_entry_stream(track, iso_name, start_lba * SECTOR_SIZE)
            sector_lba, offset = divmod(entry, SECTOR_SIZE)
            track.seek(sector_lba * SECTOR_SIZE)
            sector = bytearray(track.read(SECTOR_SIZE))
            old_lba = struct.unpack_from("<I", sector, offset + 2)[0]
            old_size = struct.unpack_from("<I", sector, offset + 10)[0]
            if old_size != len(source):
                raise ValueError(f"{iso_name!r}: source size mismatch {old_size} != {len(source)}")
            struct.pack_into("<I", sector, offset + 2, start_lba)
            struct.pack_into(">I", sector, offset + 6, start_lba)
            struct.pack_into("<I", sector, offset + 10, len(patched))
            struct.pack_into(">I", sector, offset + 14, len(patched))
            rebuild_mode2_form1(sector)
            track.seek(sector_lba * SECTOR_SIZE)
            track.write(sector)
            manifest.append(
                {
                    "iso_name": iso_name.decode("ascii"),
                    "old_lba": old_lba,
                    "old_size": old_size,
                    "new_lba": start_lba,
                    "new_size": len(patched),
                    "sectors": count,
                    "patched_path": patched_name,
                    "patched_sha256": hashlib.sha256(patched).hexdigest(),
                }
            )
    return {
        "track": str(output_track),
        "track_sha256": sha256_file(output_track),
        "files": manifest,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base_track", type=Path)
    ap.add_argument("output_track", type=Path)
    ap.add_argument("--file", action="append", nargs=3, metavar=("ISO_NAME", "SOURCE", "PATCHED"), required=True)
    args = ap.parse_args()
    try:
        result = relocate_files(
            args.base_track,
            args.output_track,
            [(iso_name, Path(source), Path(patched)) for iso_name, source, patched in args.file],
        )
    except ValueError as exc:
        ap.error(str(exc))
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
