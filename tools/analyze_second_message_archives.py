#!/usr/bin/env python3
"""Inspect and safely rebuild SECOND's battle/death message archives.

This module exists because neither BMESS2.BIN nor 2_DEAD.BIN is a flat pool
that may be split at every apparent FF byte.

BMESS2.BIN
============

The outer table contains 400 little-endian u32 offsets relative to the address
of each table field.  Entries 0..398 point to 399 CPE blocks and entry 399 is
the EOF sentinel.  Every original block has this shape::

    43 50 45 01             CPE header
    08 00                   select unit 0
    01                      load-data chunk
    00 00 00 00             load address (relative payload, always zero here)
    ss ss ss ss             payload size
    ...payload...
    00                      CPE EOF command

The payload starts with ten self-relative u16 dispatch offsets, then two
six-byte selector tables.  The selector tables end in an id of FFFF.  Dialogue
is referenced by ten-byte leaf nodes::

    tt tt aa aa bb bb oo oo oo oo

``tt`` is 0010 or 0011 and ``oo`` is an absolute u32 offset from the start of
the CPE payload to a quoted, FF-terminated message.  The other node fields must
be preserved; they select battle context/voice variants.

Existing graph bytes must not move.  The runtime also loads up to four battle
message blocks into buffers spaced 0x3000 bytes apart (SECOND.WAR calls at
0x800D6A60, 0x800D6ADC, 0x800D6B14, and 0x800D6B4C).  Consequently, blind
append-and-retarget is structurally valid but can overwrite the next runtime
slot.  The production strategy is hole-repack-and-retarget: reclaim selected
source text ranges, best-fit the expanded translations into those holes, append
only the overflow, retarget every shared leaf, and reject any CPE block larger
than 0x3000 including its wrapper.  ``rebuild_bmess_repack`` implements it.

2_DEAD.BIN
===========

The first u32 is both pointer-table size (0x7FC) and the stored value of the
first relative pointer.  The runtime selects a character with ``index << 3``:
entries ``2*i`` and ``2*i+1`` are an explicit start/end pair.  Entries 0..509
therefore describe 255 slots (95 nonempty, 160 empty), and entry 510 is the
final data boundary.  The 11 bytes after that boundary are unreferenced trailing
data and must remain unchanged.  ``rebuild_dead`` repacks the 95 live records,
rewrites every pair, preserves empty slots, and appends the trailing bytes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


CONTROL_ARG_LENGTHS: dict[int, int] = {
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

CPE_MAGIC = b"CPE\x01"
CPE_FIXED_OVERHEAD = 16
BMESS_RUNTIME_SLOT_BYTES = 0x3000
BMESS_LEGACY_SCRATCH_BYTES = 0x100


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def u16(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


@dataclass(frozen=True)
class MessageRecord:
    start: int
    end: int
    glyphs: tuple[int, ...]
    controls: tuple[int, ...]


def parse_message_record(data: bytes, start: int, limit: int | None = None) -> MessageRecord:
    """Parse one renderer record, respecting two-byte glyphs and controls."""

    if limit is None:
        limit = len(data)
    if not 0 <= start < limit <= len(data):
        raise ValueError(f"invalid record bounds {start:#x}..{limit:#x}")

    glyphs: list[int] = []
    controls: list[int] = []
    cursor = start
    while cursor < limit:
        opcode = data[cursor]
        if opcode < 0xEB:
            glyphs.append(opcode)
            cursor += 1
            continue
        if opcode < 0xF6:
            if cursor + 1 >= limit:
                raise ValueError(f"truncated two-byte glyph at {cursor:#x}")
            glyphs.append(((opcode - 0xEB) << 8) | data[cursor + 1])
            cursor += 2
            continue
        if opcode == 0xFF:
            return MessageRecord(start, cursor + 1, tuple(glyphs), tuple(controls))

        arg_length = CONTROL_ARG_LENGTHS[opcode]
        if cursor + 1 + arg_length > limit:
            raise ValueError(f"truncated control {opcode:#x} at {cursor:#x}")
        controls.append(opcode)
        cursor += 1 + arg_length

    raise ValueError(f"unterminated renderer record at {start:#x}")


def validate_complete_record(raw: bytes, quoted: bool) -> MessageRecord:
    record = parse_message_record(raw, 0, len(raw))
    if record.end != len(raw):
        raise ValueError("replacement has bytes after its FF terminator")
    if quoted and (not record.glyphs or record.glyphs[0] != 0x3E or record.glyphs[-1] != 0x3F):
        raise ValueError("BMESS replacement must retain Japanese quote glyph slots 0x03E/0x03F")
    return record


@dataclass(frozen=True)
class RelativeTable:
    table_bytes: int
    stored: tuple[int, ...]
    targets: tuple[int, ...]


def parse_relative_table(data: bytes) -> RelativeTable:
    if len(data) < 4:
        raise ValueError("file is shorter than one pointer")
    table_bytes = u32(data, 0)
    if table_bytes == 0 or table_bytes % 4 or table_bytes > len(data):
        raise ValueError(f"invalid relative pointer table size {table_bytes:#x}")
    stored = tuple(u32(data, field) for field in range(0, table_bytes, 4))
    targets = tuple(field + value for field, value in zip(range(0, table_bytes, 4), stored))
    if any(target > len(data) for target in targets):
        raise ValueError("relative pointer targets beyond EOF")
    if any(a > b for a, b in zip(targets, targets[1:])):
        raise ValueError("relative pointer targets are not monotonic")
    return RelativeTable(table_bytes, stored, targets)


@dataclass(frozen=True)
class BMessBlock:
    index: int
    file_start: int
    file_end: int
    payload: bytes
    top_targets: tuple[int, ...]
    selector_table_end: int
    # payload text target -> payload offsets of all u32 leaf reference fields
    text_references: Mapping[int, tuple[int, ...]]
    text_records: Mapping[int, MessageRecord]
    unreferenced_quoted_records: Mapping[int, MessageRecord]


@dataclass(frozen=True)
class BMessArchive:
    source: bytes
    table: RelativeTable
    blocks: tuple[BMessBlock, ...]


def _try_quoted_record(payload: bytes, target: int) -> MessageRecord | None:
    if not 0 <= target < len(payload) or payload[target] != 0x3E:
        return None
    try:
        record = parse_message_record(payload, target)
    except ValueError:
        return None
    if not record.glyphs or record.glyphs[0] != 0x3E or record.glyphs[-1] != 0x3F:
        return None
    return record


def _parse_selector_tables(payload: bytes, top_targets: tuple[int, ...]) -> int:
    """Validate the two six-byte selector tables and return their end."""

    cursor = 0x14
    for _table_index in range(2):
        while True:
            if cursor + 6 > len(payload):
                raise ValueError("unterminated BMESS selector table")
            selector_id = u16(payload, cursor)
            target = u32(payload, cursor + 2)
            if target >= len(payload):
                raise ValueError(f"selector target {target:#x} is outside payload")
            cursor += 6
            if selector_id == 0xFFFF:
                break
    if cursor != min(top_targets):
        raise ValueError(
            f"selector tables end at {cursor:#x}, first dispatch node is {min(top_targets):#x}"
        )
    return cursor


def parse_bmess(data: bytes) -> BMessArchive:
    table = parse_relative_table(data)
    if table.targets[0] != table.table_bytes:
        raise ValueError("first BMESS block does not begin immediately after its pointer table")
    if table.targets[-1] != len(data):
        raise ValueError("last BMESS pointer is not the EOF sentinel")

    blocks: list[BMessBlock] = []
    for index, (start, end) in enumerate(zip(table.targets, table.targets[1:])):
        raw = data[start:end]
        if len(raw) < CPE_FIXED_OVERHEAD or raw[:4] != CPE_MAGIC:
            raise ValueError(f"BMESS block {index} has no CPE header")
        if raw[4:7] != b"\x08\x00\x01":
            raise ValueError(f"BMESS block {index} has unexpected CPE commands")
        if u32(raw, 7) != 0:
            raise ValueError(f"BMESS block {index} has a nonzero CPE load address")
        payload_size = u32(raw, 11)
        if payload_size + CPE_FIXED_OVERHEAD != len(raw):
            raise ValueError(
                f"BMESS block {index} CPE size {payload_size:#x} does not match interval {len(raw):#x}"
            )
        if raw[-1] != 0:
            raise ValueError(f"BMESS block {index} lacks its CPE EOF command")
        payload = raw[15:-1]
        if len(payload) < 0x14:
            raise ValueError(f"BMESS block {index} payload is too short")

        top_targets = tuple(field + u16(payload, field) for field in range(0, 0x14, 2))
        if any(target >= len(payload) for target in top_targets):
            raise ValueError(f"BMESS block {index} has an invalid dispatch target")
        selector_end = _parse_selector_tables(payload, top_targets)

        # Locate only graph leaves whose u32 target is demonstrably a complete
        # quoted renderer record.  A second pass rejects any coincidental leaf
        # signature occurring inside a message.
        provisional: list[tuple[int, int, MessageRecord]] = []
        for node in range(0, len(payload) - 9):
            node_type = u16(payload, node)
            if node_type not in (0x10, 0x11):
                continue
            target = u32(payload, node + 6)
            record = _try_quoted_record(payload, target)
            if record is not None:
                provisional.append((node + 6, target, record))

        intervals = {(record.start, record.end) for _field, _target, record in provisional}
        refs: dict[int, list[int]] = defaultdict(list)
        records: dict[int, MessageRecord] = {}
        for field, target, record in provisional:
            node = field - 6
            if any(start <= node < end for start, end in intervals):
                continue
            refs[target].append(field)
            records[target] = record

        all_quoted: dict[int, MessageRecord] = {}
        for target, value in enumerate(payload):
            if value != 0x3E:
                continue
            record = _try_quoted_record(payload, target)
            if record is not None:
                all_quoted[target] = record
        unreferenced = {
            target: record
            for target, record in all_quoted.items()
            if target not in records
        }

        blocks.append(
            BMessBlock(
                index=index,
                file_start=start,
                file_end=end,
                payload=payload,
                top_targets=top_targets,
                selector_table_end=selector_end,
                text_references={target: tuple(fields) for target, fields in refs.items()},
                text_records=records,
                unreferenced_quoted_records=unreferenced,
            )
        )

    return BMessArchive(data, table, tuple(blocks))


def analyze_bmess_runtime_scratch(
    data: bytes,
    speaker_prefix_lengths: Sequence[int],
) -> dict[str, Any]:
    """Compute the maximum battle-text scratch use for every runtime entry.

    SECOND's graph evaluator concatenates every selected leaf into one scratch
    buffer.  A leaf contributes the speaker prefix (without its FF), one F6,
    the quoted BMESS record (including its FF), and one additional FF list
    terminator.  The original executable gives each of modes 0..3 only 0x100
    bytes and performs no bounds check.

    This is deliberately a fail-closed evaluator for the node types reachable
    from all ten dispatch roots and both selector tables in the retail archive.
    If a future input exposes another node type, an invalid target, or a cycle,
    the build must stop until that runtime behavior has been reviewed.
    """

    if len(speaker_prefix_lengths) != 400:
        raise ValueError(
            f"SECOND speaker table must contain 400 names, got {len(speaker_prefix_lengths)}"
        )
    if any(length < 0 for length in speaker_prefix_lengths):
        raise ValueError("speaker prefix lengths must be nonnegative")

    archive = parse_bmess(data)
    start_count = 0
    reachable_types: Counter[int] = Counter()
    results: list[dict[str, Any]] = []

    for block in archive.blocks:
        payload = block.payload
        starts = set(block.top_targets)
        cursor = 0x14
        for _table_index in range(2):
            while True:
                if cursor + 6 > len(payload):
                    raise ValueError(
                        f"BMESS block {block.index} has a truncated selector table"
                    )
                selector_id = u16(payload, cursor)
                starts.add(u32(payload, cursor + 2))
                cursor += 6
                if selector_id == 0xFFFF:
                    break

        start_count += len(starts)
        memo: dict[int, tuple[int, int, tuple[int, ...]]] = {}
        active: set[int] = set()

        def require_range(position: int, size: int) -> None:
            if position < 0 or position + size > len(payload):
                raise ValueError(
                    f"BMESS block {block.index} graph range "
                    f"{position:#x}..{position + size:#x} is outside payload"
                )

        def target_at(position: int) -> int:
            require_range(position, 4)
            target = u32(payload, position)
            if target >= len(payload):
                raise ValueError(
                    f"BMESS block {block.index} graph target {target:#x} is outside payload"
                )
            return target

        def visit(position: int) -> tuple[int, int, tuple[int, ...]]:
            if position in memo:
                return memo[position]
            if position in active:
                raise ValueError(
                    f"BMESS block {block.index} graph cycle reaches {position:#x}"
                )
            require_range(position, 2)
            active.add(position)
            node_type = u16(payload, position)
            reachable_types[node_type] += 1
            weight = 0
            leaf_count = 0

            if node_type in (0x00, 0x02, 0x04, 0x06, 0x09):
                require_range(position, 8)
                successors = (position + 8, target_at(position + 4))
            elif node_type == 0x0D:
                require_range(position, 4)
                choice_count = u16(payload, position + 2)
                if choice_count == 0:
                    raise ValueError(
                        f"BMESS block {block.index} random node {position:#x} has no choices"
                    )
                require_range(position + 4, choice_count * 4)
                successors = tuple(
                    target_at(position + 4 + choice * 4)
                    for choice in range(choice_count)
                )
            elif node_type in (0x10, 0x11):
                require_range(position, 10)
                speaker_index = u16(payload, position + 2) >> 6
                if speaker_index >= len(speaker_prefix_lengths):
                    raise ValueError(
                        f"BMESS block {block.index} leaf {position:#x} uses "
                        f"speaker {speaker_index}, outside the 400-name table"
                    )
                message_target = target_at(position + 6)
                record = parse_message_record(payload, message_target)
                weight = (
                    speaker_prefix_lengths[speaker_index]
                    + (record.end - record.start)
                    + 2
                )
                leaf_count = 1
                successors = (position + 10,)
            elif node_type == 0x14:
                require_range(position, 6)
                successors = (position + 6, target_at(position + 2))
            elif node_type == 0xFFFF:
                successors = ()
            else:
                raise ValueError(
                    f"BMESS block {block.index} runtime entry reaches unsupported "
                    f"node type {node_type:#x} at {position:#x}"
                )

            for successor in successors:
                require_range(successor, 2)
            if successors:
                tail_bytes, tail_leaves, tail_path = max(
                    (visit(successor) for successor in successors),
                    key=lambda item: (item[0], item[1]),
                )
                result = (
                    weight + tail_bytes,
                    leaf_count + tail_leaves,
                    (position,) + tail_path,
                )
            else:
                result = (weight, leaf_count, (position,))
            active.remove(position)
            memo[position] = result
            return result

        for start in sorted(starts):
            maximum_bytes, leaf_count, path = visit(start)
            results.append(
                {
                    "block_index": block.index,
                    "start": start,
                    "maximum_bytes": maximum_bytes,
                    "leaf_count": leaf_count,
                    "path": list(path),
                }
            )

    worst = max(
        results,
        key=lambda item: (item["maximum_bytes"], item["leaf_count"]),
    )
    legacy_overflows = [
        item for item in results if item["maximum_bytes"] > BMESS_LEGACY_SCRATCH_BYTES
    ]
    return {
        "block_count": len(archive.blocks),
        "unique_runtime_start_count": start_count,
        "reachable_node_types": {
            f"0x{node_type:04X}": count
            for node_type, count in sorted(reachable_types.items())
        },
        "maximum_bytes": worst["maximum_bytes"],
        "maximum_leaf_count": worst["leaf_count"],
        "worst": worst,
        "legacy_slot_bytes": BMESS_LEGACY_SCRATCH_BYTES,
        "legacy_overflow_count": len(legacy_overflows),
        "legacy_overflows": legacy_overflows,
    }


def rebuild_bmess_append(
    data: bytes,
    replacements: Mapping[tuple[int, int], bytes],
) -> bytes:
    """Append expanded BMESS records and retarget graph leaves safely.

    Replacement keys are ``(block_index, original_payload_text_offset)``.
    All leaves that shared a source record continue to share one replacement.
    Existing CPE payload bytes never move.
    """

    archive = parse_bmess(data)
    remaining = set(replacements)
    rebuilt_blocks: list[bytes] = []

    for block in archive.blocks:
        payload = bytearray(block.payload)
        selected = sorted(
            target for owner, target in remaining if owner == block.index
        )
        for target in selected:
            key = (block.index, target)
            if target not in block.text_references:
                raise ValueError(f"replacement target does not name a BMESS leaf: {key}")
            replacement = replacements[key]
            validate_complete_record(replacement, quoted=True)
            new_target = len(payload)
            payload.extend(replacement)
            for field in block.text_references[target]:
                struct.pack_into("<I", payload, field, new_target)
            remaining.remove(key)

        original_raw = data[block.file_start:block.file_end]
        rebuilt = original_raw[:11] + struct.pack("<I", len(payload)) + bytes(payload) + b"\x00"
        rebuilt_blocks.append(rebuilt)

    if remaining:
        raise ValueError(f"unmatched BMESS replacements: {sorted(remaining)[:8]!r}")

    table_bytes = archive.table.table_bytes
    output = bytearray(data[:table_bytes])
    new_targets: list[int] = []
    for raw in rebuilt_blocks:
        new_targets.append(len(output))
        output.extend(raw)
    new_targets.append(len(output))
    for index, target in enumerate(new_targets):
        field = index * 4
        struct.pack_into("<I", output, field, target - field)

    if any(len(raw) > BMESS_RUNTIME_SLOT_BYTES for raw in rebuilt_blocks):
        sizes = [len(raw) for raw in rebuilt_blocks]
        offender = max(range(len(sizes)), key=sizes.__getitem__)
        raise ValueError(
            f"BMESS block {offender} grew to {sizes[offender]:#x}; "
            f"runtime slot is only {BMESS_RUNTIME_SLOT_BYTES:#x} bytes"
        )

    # Structural round trip is part of the operation, not an optional test.
    parse_bmess(bytes(output))
    return bytes(output)


def rebuild_bmess_repack(
    data: bytes,
    replacements: Mapping[tuple[int, int], bytes],
) -> bytes:
    """Reclaim source text holes, place expanded records, and retarget leaves.

    Unlike blind append, this keeps each CPE block inside the four 0x3000-byte
    runtime slots observed in SECOND.WAR.  Only selected, directly referenced
    text ranges are reclaimed.  Graph nodes, selector tables, unselected text,
    and all 54 unreferenced quoted records remain byte-identical.
    """

    archive = parse_bmess(data)
    remaining = set(replacements)
    rebuilt_blocks: list[bytes] = []

    for block in archive.blocks:
        selected = sorted(
            target for owner, target in remaining if owner == block.index
        )
        payload = bytearray(block.payload)
        if selected:
            records: list[tuple[int, int, int, bytes]] = []
            for target in selected:
                key = (block.index, target)
                if target not in block.text_references:
                    raise ValueError(f"replacement target does not name a BMESS leaf: {key}")
                replacement = replacements[key]
                validate_complete_record(replacement, quoted=True)
                source_record = block.text_records[target]
                records.append((source_record.start, source_record.end, target, replacement))
                remaining.remove(key)

            # Selected records are distinct storage objects even when several
            # leaf nodes alias one target.  Adjacent records form one reusable
            # hole, which greatly reduces fragmentation for expanded Hangul.
            records.sort()
            holes: list[list[int]] = []  # start, end, allocation cursor
            for start, end, _target, _replacement in records:
                if holes and holes[-1][1] >= start:
                    # Block 190 deliberately has one quoted record beginning
                    # inside another (two leaf nodes share the quoted suffix).
                    # Reclaim the union once, then place both translations as
                    # independent records.
                    holes[-1][1] = max(holes[-1][1], end)
                else:
                    holes.append([start, end, start])
            for start, end, _cursor in holes:
                payload[start:end] = bytes(end - start)

            # Best-fit decreasing leaves the smallest possible fragments in
            # the interleaved node/text layout.  Overflow is the only data
            # appended to the original payload.
            placements: dict[int, int] = {}
            for _start, _end, target, replacement in sorted(
                records, key=lambda item: (-len(item[3]), item[2])
            ):
                candidates = [
                    (end - cursor, index)
                    for index, (_hole_start, end, cursor) in enumerate(holes)
                    if end - cursor >= len(replacement)
                ]
                if candidates:
                    _remaining_bytes, hole_index = min(candidates)
                    hole_start, hole_end, cursor = holes[hole_index]
                    placement = cursor
                    payload[placement:placement + len(replacement)] = replacement
                    holes[hole_index] = [hole_start, hole_end, cursor + len(replacement)]
                else:
                    placement = len(payload)
                    payload.extend(replacement)
                placements[target] = placement

            for target, placement in placements.items():
                for field in block.text_references[target]:
                    struct.pack_into("<I", payload, field, placement)

        original_raw = data[block.file_start:block.file_end]
        rebuilt = original_raw[:11] + struct.pack("<I", len(payload)) + bytes(payload) + b"\x00"
        if len(rebuilt) > BMESS_RUNTIME_SLOT_BYTES:
            raise ValueError(
                f"BMESS block {block.index} grew to {len(rebuilt):#x}; "
                f"runtime slot is only {BMESS_RUNTIME_SLOT_BYTES:#x} bytes"
            )
        rebuilt_blocks.append(rebuilt)

    if remaining:
        raise ValueError(f"unmatched BMESS replacements: {sorted(remaining)[:8]!r}")

    table_bytes = archive.table.table_bytes
    output = bytearray(data[:table_bytes])
    new_targets: list[int] = []
    for raw in rebuilt_blocks:
        new_targets.append(len(output))
        output.extend(raw)
    new_targets.append(len(output))
    for index, target in enumerate(new_targets):
        field = index * 4
        struct.pack_into("<I", output, field, target - field)

    parse_bmess(bytes(output))
    return bytes(output)


@dataclass(frozen=True)
class DeadArchive:
    source: bytes
    table: RelativeTable
    slots: tuple[tuple[int, int], ...]
    records: Mapping[int, MessageRecord]
    trailing_start: int
    trailing: bytes


def parse_dead(data: bytes) -> DeadArchive:
    table = parse_relative_table(data)
    if table.targets[0] != table.table_bytes:
        raise ValueError("2_DEAD text does not start immediately after its pointer table")
    if len(table.targets) % 2 != 1:
        raise ValueError("2_DEAD must contain start/end pairs plus one final boundary")
    slots = tuple(
        (table.targets[index], table.targets[index + 1])
        for index in range(0, len(table.targets) - 1, 2)
    )
    records: dict[int, MessageRecord] = {}
    for slot_index, (start, end) in enumerate(slots):
        if start == end:
            continue
        if start > end:
            raise ValueError(f"2_DEAD slot {slot_index} has reversed bounds")
        if start in records:
            raise ValueError(f"2_DEAD live slot {slot_index} aliases another record")
        record = parse_message_record(data, start, end)
        if record.end != end:
            raise ValueError(
                f"2_DEAD record {start:#x} ends at {record.end:#x}, interval ends at {end:#x}"
            )
        records[start] = record
    trailing_start = table.targets[-1]
    if any(end > trailing_start for _start, end in slots):
        raise ValueError("2_DEAD slot extends beyond the final data boundary")
    return DeadArchive(
        data,
        table,
        slots,
        records,
        trailing_start,
        data[trailing_start:],
    )


def rebuild_dead(data: bytes, replacements: Mapping[int, bytes]) -> bytes:
    """Repack expanded 2_DEAD start/end pairs and preserve trailing data."""

    archive = parse_dead(data)
    unknown = set(replacements) - set(archive.records)
    if unknown:
        raise ValueError(f"unmatched 2_DEAD replacement targets: {sorted(unknown)[:8]!r}")

    output = bytearray(data[:archive.table.table_bytes])
    new_targets = [0] * len(archive.table.targets)
    for slot_index, (old_start, old_end) in enumerate(archive.slots):
        new_targets[slot_index * 2] = len(output)
        if old_start != old_end:
            record = archive.records[old_start]
            raw = replacements.get(old_start, data[record.start:record.end])
            validate_complete_record(raw, quoted=False)
            output.extend(raw)
        new_targets[slot_index * 2 + 1] = len(output)
    new_targets[-1] = len(output)
    output.extend(archive.trailing)

    for index, target in enumerate(new_targets):
        field = index * 4
        struct.pack_into("<I", output, field, target - field)

    parse_dead(bytes(output))
    return bytes(output)


def report(bmess: BMessArchive, dead: DeadArchive) -> dict[str, object]:
    bm_ref_count = sum(
        len(fields)
        for block in bmess.blocks
        for fields in block.text_references.values()
    )
    bm_unique_count = sum(len(block.text_records) for block in bmess.blocks)
    bm_controls = Counter(
        control
        for block in bmess.blocks
        for record in block.text_records.values()
        for control in record.controls
    )
    dead_controls = Counter(
        control for record in dead.records.values() for control in record.controls
    )
    unreferenced = [
        {
            "block_index": block.index,
            "payload_offset": f"0x{target:X}",
            "file_offset": f"0x{block.file_start + 15 + target:X}",
            "byte_length": record.end - record.start,
        }
        for block in bmess.blocks
        for target, record in sorted(block.unreferenced_quoted_records.items())
    ]
    return {
        "bmess": {
            "sha256": sha256(bmess.source),
            "size": len(bmess.source),
            "pointer_table_bytes": bmess.table.table_bytes,
            "pointer_count_including_eof": len(bmess.table.targets),
            "cpe_block_count": len(bmess.blocks),
            "leaf_reference_count": bm_ref_count,
            "unique_referenced_texts_per_block": bm_unique_count,
            "max_cpe_block_bytes": max(
                block.file_end - block.file_start for block in bmess.blocks
            ),
            "runtime_slot_bytes": BMESS_RUNTIME_SLOT_BYTES,
            "control_opcodes": {f"0x{key:02X}": value for key, value in sorted(bm_controls.items())},
            "unreferenced_quoted_record_count": len(unreferenced),
            "unreferenced_quoted_records": unreferenced,
            "rebuild_strategy": "repack selected text holes; retarget leaf u32; enforce 0x3000 runtime slot; update CPE size and outer relative pointers",
        },
        "dead": {
            "sha256": sha256(dead.source),
            "size": len(dead.source),
            "pointer_table_bytes": dead.table.table_bytes,
            "pointer_count": len(dead.table.targets),
            "slot_count": len(dead.slots),
            "unique_record_count": len(dead.records),
            "empty_slot_count": sum(start == end for start, end in dead.slots),
            "trailing_byte_count": len(dead.trailing),
            "control_opcodes": {f"0x{key:02X}": value for key, value in sorted(dead_controls.items())},
            "rebuild_strategy": "repack 255 start/end pairs; preserve 160 empty slots and unreferenced trailing bytes",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bmess",
        type=Path,
        default=Path("korean_patch/extracted/BMESS2.BIN"),
    )
    parser.add_argument(
        "--dead",
        type=Path,
        default=Path("korean_patch/extracted/SECOND/2_DEAD.BIN"),
    )
    args = parser.parse_args()

    bmess = parse_bmess(args.bmess.read_bytes())
    dead = parse_dead(args.dead.read_bytes())
    document = report(bmess, dead)

    # These original-disc counts catch a parser regression before a builder can
    # silently patch a CPE header or an internal selector table.
    expected = {
        "pointer_count_including_eof": 400,
        "cpe_block_count": 399,
        "leaf_reference_count": 19913,
        "unique_referenced_texts_per_block": 17364,
        "unreferenced_quoted_record_count": 54,
    }
    for key, value in expected.items():
        if document["bmess"][key] != value:  # type: ignore[index]
            raise ValueError(
                f"original BMESS invariant {key} changed: "
                f"{document['bmess'][key]} != {value}"  # type: ignore[index]
            )
    if document["dead"]["pointer_count"] != 511:  # type: ignore[index]
        raise ValueError("original 2_DEAD pointer count changed")
    if document["dead"]["slot_count"] != 255:  # type: ignore[index]
        raise ValueError("original 2_DEAD slot count changed")
    if document["dead"]["unique_record_count"] != 95:  # type: ignore[index]
        raise ValueError("original 2_DEAD unique record count changed")
    if document["dead"]["empty_slot_count"] != 160:  # type: ignore[index]
        raise ValueError("original 2_DEAD empty slot count changed")
    if document["dead"]["trailing_byte_count"] != 11:  # type: ignore[index]
        raise ValueError("original 2_DEAD trailing byte count changed")

    print(json.dumps(document, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
