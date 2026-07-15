#!/usr/bin/env python3
"""List and extract the ISO9660 filesystem from a raw PS1 MODE2/2352 BIN.

The extractor maps each 2352-byte raw sector to its 2048-byte ISO logical
payload.  It is intended for executable/resource analysis; CD-XA Form 2 media
streams should be preserved from the original BIN rather than rebuilt from the
files emitted here.
"""

from __future__ import annotations

import argparse
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator


RAW_SECTOR_SIZE = 2352
LOGICAL_SECTOR_SIZE = 2048
MODE2_DATA_OFFSET = 24
SYNC = bytes.fromhex("00ffffffffffffffffffff00")


@dataclass(frozen=True)
class Entry:
    path: str
    lba: int
    size: int
    flags: int
    file_unit_size: int
    interleave_gap: int

    @property
    def is_dir(self) -> bool:
        return bool(self.flags & 0x02)


class RawMode2Image:
    def __init__(self, path: Path):
        self.path = path
        self.handle: BinaryIO | None = None
        self.sector_count = 0

    def __enter__(self) -> "RawMode2Image":
        size = self.path.stat().st_size
        if size % RAW_SECTOR_SIZE:
            raise ValueError(
                f"{self.path} is not an integral number of 2352-byte sectors"
            )
        self.sector_count = size // RAW_SECTOR_SIZE
        self.handle = self.path.open("rb")
        return self

    def __exit__(self, *_: object) -> None:
        if self.handle:
            self.handle.close()
            self.handle = None

    def read_sector(self, lba: int) -> bytes:
        if self.handle is None:
            raise RuntimeError("image is not open")
        self.handle.seek(lba * RAW_SECTOR_SIZE)
        raw = self.handle.read(RAW_SECTOR_SIZE)
        if len(raw) != RAW_SECTOR_SIZE:
            raise EOFError(f"short read at LBA {lba}")
        if raw[:12] != SYNC or raw[15] != 2:
            raise ValueError(f"LBA {lba} is not a MODE2 sector")
        return raw[MODE2_DATA_OFFSET : MODE2_DATA_OFFSET + LOGICAL_SECTOR_SIZE]

    def read_extent(self, lba: int, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining:
            sector = self.read_sector(lba)
            take = min(remaining, LOGICAL_SECTOR_SIZE)
            chunks.append(sector[:take])
            remaining -= take
            lba += 1
        return b"".join(chunks)

    def copy_extent(self, lba: int, size: int, output: BinaryIO) -> str:
        digest = hashlib.sha256()
        remaining = size
        while remaining:
            sector = self.read_sector(lba)
            take = min(remaining, LOGICAL_SECTOR_SIZE)
            data = sector[:take]
            output.write(data)
            digest.update(data)
            remaining -= take
            lba += 1
        return digest.hexdigest()

    def extent_in_range(self, lba: int, size: int) -> bool:
        sectors = (size + LOGICAL_SECTOR_SIZE - 1) // LOGICAL_SECTOR_SIZE
        return 0 <= lba and lba + sectors <= self.sector_count


def u32_both(data: bytes, offset: int) -> int:
    little = int.from_bytes(data[offset : offset + 4], "little")
    big = int.from_bytes(data[offset + 4 : offset + 8], "big")
    if little != big:
        raise ValueError(f"both-endian value mismatch at record offset {offset}")
    return little


def parse_record(record: bytes, parent: str) -> Entry:
    if len(record) < 34:
        raise ValueError("truncated ISO9660 directory record")
    name_len = record[32]
    raw_name = record[33 : 33 + name_len]
    if raw_name == b"\x00":
        name = "."
    elif raw_name == b"\x01":
        name = ".."
    else:
        name = raw_name.decode("ascii", "replace").split(";", 1)[0]
    path = f"{parent}/{name}" if parent else name
    return Entry(
        path=path,
        lba=u32_both(record, 2),
        size=u32_both(record, 10),
        flags=record[25],
        file_unit_size=record[26],
        interleave_gap=record[27],
    )


def iter_directory_records(data: bytes) -> Iterator[bytes]:
    position = 0
    while position < len(data):
        record_len = data[position]
        if record_len == 0:
            position = ((position // LOGICAL_SECTOR_SIZE) + 1) * LOGICAL_SECTOR_SIZE
            continue
        end = position + record_len
        if end > len(data):
            raise ValueError("directory record extends beyond directory extent")
        yield data[position:end]
        position = end


def read_tree(image: RawMode2Image) -> tuple[str, list[Entry]]:
    pvd = image.read_sector(16)
    if pvd[:7] != b"\x01CD001\x01":
        raise ValueError("ISO9660 primary volume descriptor not found at LBA 16")
    volume_id = pvd[40:72].decode("ascii", "replace").rstrip()
    root_len = pvd[156]
    root = parse_record(pvd[156 : 156 + root_len], "")
    entries: list[Entry] = []
    visited: set[tuple[int, int]] = set()

    def walk(directory: Entry, parent: str) -> None:
        key = (directory.lba, directory.size)
        if key in visited:
            return
        visited.add(key)
        data = image.read_extent(directory.lba, directory.size)
        for record in iter_directory_records(data):
            entry = parse_record(record, parent)
            leaf = entry.path.rsplit("/", 1)[-1]
            if leaf in {".", ".."}:
                continue
            entries.append(entry)
            if entry.is_dir:
                walk(entry, entry.path)

    walk(root, "")
    return volume_id, entries


def safe_output_path(root: Path, iso_path: str) -> Path:
    parts = [part for part in iso_path.replace("\\", "/").split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError(f"unsafe ISO path: {iso_path}")
    return root.joinpath(*parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bin", type=Path, help="raw MODE2/2352 data-track BIN")
    parser.add_argument("output", type=Path, help="directory for extracted files")
    parser.add_argument(
        "--skip-larger-than",
        type=int,
        default=0,
        metavar="BYTES",
        help="do not extract files larger than this size (0 extracts all)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="write a tab-separated extraction manifest",
    )
    args = parser.parse_args()

    args.output.mkdir(parents=True, exist_ok=True)
    rows = ["path\ttype\tlba\tsize\tflags\tstatus\tsha256"]
    with RawMode2Image(args.bin) as image:
        volume_id, entries = read_tree(image)
        print(f"Volume: {volume_id}")
        print(f"Entries: {len(entries)}")
        for entry in entries:
            kind = "dir" if entry.is_dir else "file"
            if entry.file_unit_size or entry.interleave_gap:
                raise NotImplementedError(
                    f"interleaved extent is unsupported: {entry.path}"
                )
            if entry.is_dir:
                safe_output_path(args.output, entry.path).mkdir(
                    parents=True, exist_ok=True
                )
                rows.append(
                    f"{entry.path}\t{kind}\t{entry.lba}\t{entry.size}\t"
                    f"0x{entry.flags:02x}\tdirectory\t"
                )
                continue
            if args.skip_larger_than and entry.size > args.skip_larger_than:
                print(f"SKIP {entry.path} ({entry.size} bytes)")
                rows.append(
                    f"{entry.path}\t{kind}\t{entry.lba}\t{entry.size}\t"
                    f"0x{entry.flags:02x}\tskipped-size\t"
                )
                continue
            if not image.extent_in_range(entry.lba, entry.size):
                print(
                    f"SKIP {entry.path} (extent is outside the data-track BIN; "
                    "likely an audio-track alias)"
                )
                rows.append(
                    f"{entry.path}\t{kind}\t{entry.lba}\t{entry.size}\t"
                    f"0x{entry.flags:02x}\toutside-data-track\t"
                )
                continue
            destination = safe_output_path(args.output, entry.path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("wb") as output:
                sha256 = image.copy_extent(entry.lba, entry.size, output)
            print(f"EXTRACT {entry.path} ({entry.size} bytes)")
            rows.append(
                f"{entry.path}\t{kind}\t{entry.lba}\t{entry.size}\t"
                f"0x{entry.flags:02x}\textracted\t{sha256}"
            )

    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
        print(f"Manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
