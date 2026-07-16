#!/usr/bin/env python3
"""Replace fixed-size PS-X EXEs inside a MODE2/2352 track and rebuild ECC/EDC."""

from __future__ import annotations

import argparse
import hashlib
import shutil
from pathlib import Path


SECTOR_SIZE = 2352
USER_DATA_OFFSET = 0x18
USER_DATA_SIZE = 0x800
EDC_OFFSET = 0x818
ECC_P_OFFSET = 0x81C
ECC_Q_OFFSET = 0x8C8

EXE_LAYOUT = {
    Path("SLPS_020.70"): 239346,
    Path("TR.WAR"): 239719,
    Path("EX/EX.WAR"): 25521,
    Path("SECOND/SECOND.WAR"): 24922,
    Path("THIRD/THIRD.WAR"): 26827,
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def make_edc_table() -> list[int]:
    table: list[int] = []
    for value in range(256):
        current = value
        for _ in range(8):
            current = (current >> 1) ^ (0xD8018001 if current & 1 else 0)
        table.append(current & 0xFFFFFFFF)
    return table


def make_ecc_tables() -> tuple[list[int], list[int]]:
    forward = [0] * 256
    backward = [0] * 256
    for value in range(256):
        doubled = value << 1
        if doubled & 0x100:
            doubled ^= 0x11D
        forward[value] = doubled
        backward[value ^ doubled] = value
    return forward, backward


EDC_TABLE = make_edc_table()
ECC_FORWARD, ECC_BACKWARD = make_ecc_tables()


def compute_edc(data: bytes) -> int:
    value = 0
    for byte in data:
        value = (value >> 8) ^ EDC_TABLE[(value ^ byte) & 0xFF]
    return value


def compute_ecc(
    address: bytes,
    data: bytes,
    major_count: int,
    minor_count: int,
    major_mult: int,
    minor_inc: int,
) -> bytes:
    size = major_count * minor_count
    output = bytearray(major_count * 2)
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for _ in range(minor_count):
            byte = address[index] if index < 4 else data[index - 4]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= byte
            ecc_b ^= byte
            ecc_a = ECC_FORWARD[ecc_a]
        ecc_a = ECC_BACKWARD[ECC_FORWARD[ecc_a] ^ ecc_b]
        output[major] = ecc_a
        output[major + major_count] = ecc_a ^ ecc_b
    return bytes(output)


def rebuild_mode2_form1(sector: bytearray) -> None:
    if len(sector) != SECTOR_SIZE:
        raise ValueError("truncated raw sector")
    if sector[0x0F] != 2:
        raise ValueError(f"expected MODE2 sector, got mode {sector[0x0F]}")
    if sector[0x12] & 0x20:
        raise ValueError("MODE2 Form 2 sector cannot contain a PS-X EXE")
    if sector[0x10:0x14] != sector[0x14:0x18]:
        raise ValueError("MODE2 subheader copies do not match")

    edc = compute_edc(sector[0x10:EDC_OFFSET])
    sector[EDC_OFFSET:ECC_P_OFFSET] = edc.to_bytes(4, "little")
    # In MODE2 Form 1, the address/header contribution is zero for ECC.
    address = bytes(4)
    sector[ECC_P_OFFSET:ECC_Q_OFFSET] = compute_ecc(
        address,
        sector[0x10:ECC_P_OFFSET],
        86,
        24,
        2,
        86,
    )
    sector[ECC_Q_OFFSET:SECTOR_SIZE] = compute_ecc(
        address,
        sector[0x10:ECC_Q_OFFSET],
        52,
        43,
        86,
        88,
    )


def patch_one_executable(
    track,
    lba: int,
    source_exe: bytes,
    patched_exe: bytes,
    label: str,
) -> None:
    if len(source_exe) != len(patched_exe):
        raise ValueError(f"{label}: source and patched EXE sizes differ")
    if len(source_exe) % USER_DATA_SIZE:
        raise ValueError(f"{label}: EXE size is not a multiple of 2048")

    extracted = bytearray()
    for sector_index in range(len(source_exe) // USER_DATA_SIZE):
        track.seek((lba + sector_index) * SECTOR_SIZE)
        sector = bytearray(track.read(SECTOR_SIZE))
        if len(sector) != SECTOR_SIZE:
            raise ValueError(f"{label}: track ends inside sector {sector_index}")
        original_chunk = sector[
            USER_DATA_OFFSET : USER_DATA_OFFSET + USER_DATA_SIZE
        ]
        extracted.extend(original_chunk)
        expected = source_exe[
            sector_index * USER_DATA_SIZE : (sector_index + 1) * USER_DATA_SIZE
        ]
        if original_chunk != expected:
            raise ValueError(
                f"{label}: raw sector {sector_index} does not match extracted source"
            )

        replacement = patched_exe[
            sector_index * USER_DATA_SIZE : (sector_index + 1) * USER_DATA_SIZE
        ]
        if replacement == original_chunk:
            continue
        sector[USER_DATA_OFFSET : USER_DATA_OFFSET + USER_DATA_SIZE] = replacement
        rebuild_mode2_form1(sector)
        track.seek((lba + sector_index) * SECTOR_SIZE)
        track.write(sector)

    if bytes(extracted) != source_exe:
        raise AssertionError(f"{label}: source verification failed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_track", type=Path)
    parser.add_argument("output_track", type=Path)
    parser.add_argument("source_extracted_dir", type=Path)
    parser.add_argument("patched_extracted_dir", type=Path)
    args = parser.parse_args()

    if args.source_track.resolve() == args.output_track.resolve():
        parser.error("output_track must not overwrite source_track")
    args.output_track.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.source_track, args.output_track)

    with args.output_track.open("r+b") as track:
        for relative_path, lba in EXE_LAYOUT.items():
            source_path = args.source_extracted_dir / relative_path
            patched_path = args.patched_extracted_dir / relative_path
            source = source_path.read_bytes()
            patched = patched_path.read_bytes()
            patch_one_executable(track, lba, source, patched, str(relative_path))
            print(
                f"{relative_path}: LBA {lba}, {len(source) // USER_DATA_SIZE} sectors, "
                f"SHA-256 {hashlib.sha256(patched).hexdigest()}"
            )

    print(f"Source track SHA-256: {sha256_file(args.source_track)}")
    print(f"Patched track SHA-256: {sha256_file(args.output_track)}")
    print(f"Patched track: {args.output_track.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
