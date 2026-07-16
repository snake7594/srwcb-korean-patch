#!/usr/bin/env python3
"""Inspect and safely relocate FF-terminated text pools in an SRWCB SCE file.

This is an analysis/reference implementation.  It does not write a patched
file unless another tool imports :func:`relocate_sce` and supplies complete
replacement records.  The important format details are:

* the file-level u32 table stores pointers relative to each table field;
* scenario targets are pairs (block start, meaningful text-pool end);
* block+0 stores the text-pool start relative to the block;
* B1/B3/B4 VM commands store a u16 pointer relative to the pointer operand;
* text records must be tokenised, because FF can be a control/glyph operand;
* every following scenario block must remain four-byte aligned.
"""

from __future__ import annotations

import argparse
import json
import struct
from dataclasses import dataclass
from pathlib import Path


CONTROL_ARG_LENGTHS = {
    0xF6: 0,
    0xF7: 0,
    0xF8: 1,
    0xF9: 1,
    0xFA: 0,
    0xFB: 2,
    0xFC: 2,
    0xFD: 2,
    0xFE: 1,
}
TEXT_POINTER_OPCODES = frozenset((0xB1, 0xB3, 0xB4))
SCENARIO_HEADER_SIZE = 0x38


@dataclass(frozen=True)
class Record:
    start: int
    end: int


@dataclass(frozen=True)
class TextReference:
    opcode_offset: int
    operand_offset: int
    target: int
    opcode: int


@dataclass(frozen=True)
class Scenario:
    index: int
    block_start: int
    pool_start: int
    pool_end: int
    record_data_end: int
    next_block_start: int | None
    header_targets_relative: tuple[int, ...]
    records: tuple[Record, ...]
    references: tuple[TextReference, ...]


def pointer_targets(data: bytes) -> list[int]:
    if len(data) < 4:
        raise ValueError("SCE file is too short")
    table_bytes = struct.unpack_from("<I", data, 0)[0]
    if table_bytes == 0 or table_bytes % 8 or table_bytes > len(data):
        raise ValueError(f"invalid SCE pointer table length {table_bytes:#x}")
    return [
        field + struct.unpack_from("<I", data, field)[0]
        for field in range(0, table_bytes, 4)
    ]


def parse_records(data: bytes, start: int, end: int) -> tuple[Record, ...]:
    """Parse renderer bytecode; operand FF bytes are never terminators."""
    records: list[Record] = []
    record_start = cursor = start
    while cursor < end:
        opcode = data[cursor]
        if opcode < 0xEB:
            cursor += 1
        elif opcode < 0xF6:
            cursor += 2
        elif opcode == 0xFF:
            cursor += 1
            records.append(Record(record_start, cursor))
            record_start = cursor
        else:
            cursor += 1 + CONTROL_ARG_LENGTHS[opcode]
        if cursor > end:
            raise ValueError(
                f"token at {cursor:#x} overruns text pool ending at {end:#x}"
            )
    if record_start != end:
        # SECOND scenario 27 has three zero bytes between its final FF and the
        # odd file-table target.  They are pool tail bytes, not another text
        # record.  Retain this observed quirk byte-for-byte during relocation.
        tail = data[record_start:end]
        if len(tail) > 3 or any(tail):
            raise ValueError(f"unterminated record at {record_start:#x}")
    return tuple(records)


def find_text_references(
    data: bytes, block_start: int, pool_start: int, records: tuple[Record, ...]
) -> tuple[TextReference, ...]:
    """Find VM B1/B3/B4 operands whose original target is a record start."""
    starts = {record.start for record in records}
    references: list[TextReference] = []
    for opcode_offset in range(block_start, pool_start - 2):
        opcode = data[opcode_offset]
        if opcode not in TEXT_POINTER_OPCODES:
            continue
        operand_offset = opcode_offset + 1
        displacement = struct.unpack_from("<H", data, operand_offset)[0]
        target = operand_offset + displacement
        if target in starts:
            references.append(
                TextReference(opcode_offset, operand_offset, target, opcode)
            )
    return tuple(references)


def parse_scenarios(data: bytes) -> tuple[Scenario, ...]:
    targets = pointer_targets(data)
    scenarios: list[Scenario] = []
    for index in range(len(targets) // 2):
        block_start = targets[index * 2]
        pool_end = targets[index * 2 + 1]
        next_block_start = (
            targets[index * 2 + 2] if index * 2 + 2 < len(targets) else None
        )
        if block_start % 4:
            raise ValueError(
                f"scenario {index} block is not four-byte aligned: {block_start:#x}"
            )
        if block_start + 4 > len(data):
            raise ValueError(f"scenario {index} block start is outside the file")
        header_targets_relative = tuple(
            field + struct.unpack_from("<I", data, block_start + field)[0]
            for field in range(0, SCENARIO_HEADER_SIZE, 4)
        )
        pool_start = block_start + struct.unpack_from("<I", data, block_start)[0]
        if not block_start < pool_start <= pool_end <= len(data):
            raise ValueError(
                f"scenario {index} has invalid pool {pool_start:#x}..{pool_end:#x}"
            )
        if any(
            target < SCENARIO_HEADER_SIZE or block_start + target > pool_start
            for target in header_targets_relative
        ):
            raise ValueError(f"scenario {index} has an invalid relative header pointer")
        records = parse_records(data, pool_start, pool_end)
        record_data_end = records[-1].end if records else pool_start
        references = find_text_references(data, block_start, pool_start, records)
        scenarios.append(
            Scenario(
                index,
                block_start,
                pool_start,
                pool_end,
                record_data_end,
                next_block_start,
                header_targets_relative,
                records,
                references,
            )
        )
    return tuple(scenarios)


def _validate_replacement(raw: bytes, source_offset: int) -> None:
    if not raw:
        raise ValueError(f"replacement at {source_offset:#x} is empty")
    records = parse_records(raw, 0, len(raw))
    if len(records) != 1:
        raise ValueError(
            f"replacement at {source_offset:#x} encodes {len(records)} records, not one"
        )


def relocate_sce(source: bytes, replacements: dict[int, bytes]) -> bytes:
    """Reference relocation algorithm for length-changing SCE replacements.

    ``replacements`` maps original absolute record starts to complete encoded
    records, including their true FF terminator.  Scenario scripts are kept at
    their original length; all direct text references and the file-level
    pointer table are rebuilt.
    """
    scenarios = parse_scenarios(source)
    known_starts = {record.start for s in scenarios for record in s.records}
    unknown = set(replacements) - known_starts
    if unknown:
        raise ValueError(
            "replacement offsets are not record starts: "
            + ", ".join(f"{offset:#x}" for offset in sorted(unknown))
        )
    for offset, raw in replacements.items():
        _validate_replacement(raw, offset)

    table_bytes = struct.unpack_from("<I", source, 0)[0]
    output = bytearray(source[:table_bytes])
    new_targets: list[int] = []

    for scenario in scenarios:
        while len(output) % 4:
            output.append(0)
        new_block_start = len(output)
        script = bytearray(source[scenario.block_start : scenario.pool_start])

        new_pool = bytearray()
        new_record_rel: dict[int, int] = {}
        for record in scenario.records:
            new_record_rel[record.start] = len(script) + len(new_pool)
            new_pool.extend(
                replacements.get(record.start, source[record.start : record.end])
            )
        new_pool.extend(source[scenario.record_data_end : scenario.pool_end])

        # B1/B3/B4 pointers are relative to the two-byte operand field, not to
        # the opcode, block, pool, or file.  Relocating both the script and its
        # pool together means this calculation can be done block-relatively.
        for reference in scenario.references:
            operand_rel = reference.operand_offset - scenario.block_start
            target_rel = new_record_rel[reference.target]
            displacement = target_rel - operand_rel
            if not 0 <= displacement <= 0xFFFF:
                raise ValueError(
                    f"scenario {scenario.index} text reference at "
                    f"{reference.operand_offset:#x} exceeds u16 after relocation: "
                    f"{displacement:#x}"
                )
            struct.pack_into("<H", script, operand_rel, displacement)

        output.extend(script)
        output.extend(new_pool)
        new_pool_end = len(output)
        new_targets.extend((new_block_start, new_pool_end))

    for index, target in enumerate(new_targets):
        field = index * 4
        displacement = target - field
        if not 0 <= displacement <= 0xFFFFFFFF:
            raise ValueError(f"file pointer {index} is outside u32 range")
        struct.pack_into("<I", output, field, displacement)

    # Reparse everything, including alignment and every relocated text target.
    reparsed = parse_scenarios(bytes(output))
    for old, new in zip(scenarios, reparsed):
        if len(old.records) != len(new.records):
            raise AssertionError(
                f"scenario {old.index} record count changed from "
                f"{len(old.records)} to {len(new.records)}"
            )
        if len(old.references) != len(new.references):
            raise AssertionError(
                f"scenario {old.index} lost text references after relocation"
            )
    return bytes(output)


def build_report(data: bytes) -> dict[str, object]:
    scenarios = parse_scenarios(data)
    opcode_counts = {f"0x{opcode:02X}": 0 for opcode in sorted(TEXT_POINTER_OPCODES)}
    for scenario in scenarios:
        for reference in scenario.references:
            opcode_counts[f"0x{reference.opcode:02X}"] += 1
    return {
        "file_size": len(data),
        "control_argument_lengths": {
            f"0x{opcode:02X}": length
            for opcode, length in CONTROL_ARG_LENGTHS.items()
        },
        "scenario_count": len(scenarios),
        "record_count": sum(len(s.records) for s in scenarios),
        "direct_text_reference_count": sum(len(s.references) for s in scenarios),
        "direct_text_reference_opcodes": opcode_counts,
        "scenarios": [
            {
                "index": s.index,
                "block_start": f"0x{s.block_start:X}",
                "block_aligned_4": s.block_start % 4 == 0,
                "pool_start": f"0x{s.pool_start:X}",
                "pool_end": f"0x{s.pool_end:X}",
                "record_count": len(s.records),
                "direct_text_reference_count": len(s.references),
                "header_pointer_targets_relative": [
                    f"0x{target:X}" for target in s.header_targets_relative
                ],
                "padding_to_next_block": (
                    None
                    if s.next_block_start is None
                    else s.next_block_start - s.pool_end
                ),
            }
            for s in scenarios
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sce", type=Path)
    parser.add_argument("--output", type=Path, help="optional UTF-8 JSON report")
    args = parser.parse_args()
    report = build_report(args.sce.read_bytes())
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
