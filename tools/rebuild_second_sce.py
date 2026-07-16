#!/usr/bin/env python3
"""Production rebuild support for length-changing SECOND/2_SCE.BIN text.

The public API is :func:`rebuild_second_sce`.  It accepts the pristine SCE
bytes and a mapping from original absolute record offsets to complete encoded
replacement records (including the real FF terminator).  Record lengths may
change freely.  The implementation delegates the format-sensitive relocation
to the independently verified analyser, then performs stricter production
checks on record identity, B1/B3/B4 references, script bytes, alignment, and
the 102-entry file pointer table.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any


TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from analyze_sce_relocation import (  # noqa: E402
    TEXT_POINTER_OPCODES,
    Scenario,
    parse_records,
    parse_scenarios,
    pointer_targets,
    relocate_sce,
)


EXPECTED_SOURCE_SIZE = 417_700
EXPECTED_SOURCE_SHA256 = (
    "83dc5fbd0a254d8537113977e6856b7169bfafe0a9fe3a60c1fa473e727b9fad"
)
EXPECTED_POINTER_COUNT = 102
EXPECTED_SCENARIO_COUNT = 51


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalise_replacements(
    replacements: Mapping[int, bytes | bytearray | memoryview],
) -> dict[int, bytes]:
    normalised: dict[int, bytes] = {}
    for offset, raw in replacements.items():
        if isinstance(offset, bool) or not isinstance(offset, int):
            raise TypeError(f"replacement offset must be int, got {offset!r}")
        if offset < 0:
            raise ValueError(f"replacement offset cannot be negative: {offset}")
        if not isinstance(raw, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"replacement at {offset:#x} must be bytes-like, got {type(raw).__name__}"
            )
        encoded = bytes(raw)
        # This uses the corrected renderer grammar, notably F7 with zero args.
        parsed = parse_records(encoded, 0, len(encoded))
        if len(parsed) != 1 or parsed[0].start != 0 or parsed[0].end != len(encoded):
            raise ValueError(
                f"replacement at {offset:#x} must encode exactly one complete record"
            )
        normalised[offset] = encoded
    return normalised


def _validate_source(source: bytes, strict_source: bool) -> tuple[Scenario, ...]:
    if strict_source:
        if len(source) != EXPECTED_SOURCE_SIZE:
            raise ValueError(
                f"unexpected original 2_SCE.BIN size: {len(source)} "
                f"(expected {EXPECTED_SOURCE_SIZE})"
            )
        digest = sha256(source)
        if digest != EXPECTED_SOURCE_SHA256:
            raise ValueError(
                "unexpected original 2_SCE.BIN SHA-256: "
                f"{digest} (expected {EXPECTED_SOURCE_SHA256})"
            )
    targets = pointer_targets(source)
    if len(targets) != EXPECTED_POINTER_COUNT:
        raise ValueError(
            f"2_SCE.BIN has {len(targets)} file pointers, expected "
            f"{EXPECTED_POINTER_COUNT}"
        )
    scenarios = parse_scenarios(source)
    if len(scenarios) != EXPECTED_SCENARIO_COUNT:
        raise ValueError(
            f"2_SCE.BIN has {len(scenarios)} scenarios, expected "
            f"{EXPECTED_SCENARIO_COUNT}"
        )
    return scenarios


def _record_locations(
    scenarios: tuple[Scenario, ...],
) -> dict[int, tuple[int, int]]:
    return {
        record.start: (scenario.index, ordinal)
        for scenario in scenarios
        for ordinal, record in enumerate(scenario.records)
    }


def _reference_identity(scenario: Scenario) -> dict[int, tuple[int, int]]:
    """Map block-relative operand -> (opcode, target record ordinal)."""
    record_ordinals = {
        record.start: ordinal for ordinal, record in enumerate(scenario.records)
    }
    return {
        reference.operand_offset - scenario.block_start: (
            reference.opcode,
            record_ordinals[reference.target],
        )
        for reference in scenario.references
    }


def _verify_script_changes(
    source: bytes,
    output: bytes,
    old: Scenario,
    new: Scenario,
) -> None:
    old_script = source[old.block_start : old.pool_start]
    new_script = output[new.block_start : new.pool_start]
    if len(old_script) != len(new_script):
        raise AssertionError(
            f"scenario {old.index} script length changed from "
            f"{len(old_script)} to {len(new_script)}"
        )
    allowed: set[int] = set()
    for reference in old.references:
        operand_rel = reference.operand_offset - old.block_start
        allowed.update((operand_rel, operand_rel + 1))
    unexpected = [
        offset
        for offset, (before, after) in enumerate(zip(old_script, new_script))
        if before != after and offset not in allowed
    ]
    if unexpected:
        preview = ", ".join(f"0x{offset:X}" for offset in unexpected[:8])
        raise AssertionError(
            f"scenario {old.index} changed non-pointer script bytes at {preview}"
        )
    if old.header_targets_relative != new.header_targets_relative:
        raise AssertionError(f"scenario {old.index} relative header targets changed")


def _verify_rebuild(
    source: bytes,
    output: bytes,
    replacements: dict[int, bytes],
    old_scenarios: tuple[Scenario, ...],
) -> tuple[tuple[Scenario, ...], list[dict[str, Any]], list[dict[str, Any]]]:
    new_targets = pointer_targets(output)
    if len(new_targets) != EXPECTED_POINTER_COUNT:
        raise AssertionError("rebuilt SCE file pointer count changed")
    new_scenarios = parse_scenarios(output)
    if len(new_scenarios) != len(old_scenarios):
        raise AssertionError("rebuilt SCE scenario count changed")
    if any(scenario.block_start % 4 for scenario in new_scenarios):
        raise AssertionError("rebuilt SCE contains an unaligned scenario block")

    old_locations = _record_locations(old_scenarios)
    unknown = set(replacements) - set(old_locations)
    if unknown:
        raise ValueError(
            "replacement offsets are not original record starts: "
            + ", ".join(f"{offset:#x}" for offset in sorted(unknown))
        )

    record_manifest: list[dict[str, Any]] = []
    scenario_manifest: list[dict[str, Any]] = []
    for old, new in zip(old_scenarios, new_scenarios):
        if len(old.records) != len(new.records):
            raise AssertionError(
                f"scenario {old.index} record count changed from "
                f"{len(old.records)} to {len(new.records)}"
            )
        if _reference_identity(old) != _reference_identity(new):
            raise AssertionError(
                f"scenario {old.index} B1/B3/B4 reference identity changed"
            )
        _verify_script_changes(source, output, old, new)

        old_tail = source[old.record_data_end : old.pool_end]
        new_tail = output[new.record_data_end : new.pool_end]
        if old_tail != new_tail:
            raise AssertionError(f"scenario {old.index} pool tail changed")

        replacement_count = 0
        pool_record_delta = 0
        for ordinal, (old_record, new_record) in enumerate(
            zip(old.records, new.records)
        ):
            source_raw = source[old_record.start : old_record.end]
            expected = replacements.get(old_record.start, source_raw)
            actual = output[new_record.start : new_record.end]
            if actual != expected:
                raise AssertionError(
                    f"scenario {old.index} record {ordinal} does not match its "
                    "source/replacement bytes"
                )
            if old_record.start in replacements:
                replacement_count += 1
                delta = len(actual) - len(source_raw)
                pool_record_delta += delta
                record_manifest.append(
                    {
                        "scenario_index": old.index,
                        "record_ordinal": ordinal,
                        "source_offset": old_record.start,
                        "source_offset_hex": f"0x{old_record.start:X}",
                        "output_offset": new_record.start,
                        "output_offset_hex": f"0x{new_record.start:X}",
                        "source_length": len(source_raw),
                        "output_length": len(actual),
                        "byte_delta": delta,
                        "source_sha256": sha256(source_raw),
                        "output_sha256": sha256(actual),
                    }
                )

        old_next_pad = (
            0
            if old.next_block_start is None
            else old.next_block_start - old.pool_end
        )
        new_next_pad = (
            0
            if new.next_block_start is None
            else new.next_block_start - new.pool_end
        )
        scenario_manifest.append(
            {
                "scenario_index": old.index,
                "source_block_start": old.block_start,
                "output_block_start": new.block_start,
                "source_pool_start": old.pool_start,
                "output_pool_start": new.pool_start,
                "source_pool_end": old.pool_end,
                "output_pool_end": new.pool_end,
                "source_record_count": len(old.records),
                "replacement_count": replacement_count,
                "record_byte_delta": pool_record_delta,
                "source_padding_to_next_block": old_next_pad,
                "output_padding_to_next_block": new_next_pad,
                "direct_text_reference_count": len(old.references),
            }
        )

    old_reference_count = sum(len(s.references) for s in old_scenarios)
    new_reference_count = sum(len(s.references) for s in new_scenarios)
    if new_reference_count != old_reference_count:
        raise AssertionError(
            f"direct text reference count changed from {old_reference_count} "
            f"to {new_reference_count}"
        )
    return new_scenarios, scenario_manifest, record_manifest


def rebuild_second_sce(
    source: bytes,
    replacements: Mapping[int, bytes | bytearray | memoryview],
    *,
    strict_source: bool = True,
) -> tuple[bytes, dict[str, Any]]:
    """Rebuild SECOND/2_SCE.BIN with arbitrary-length encoded records.

    Args:
        source: Pristine original ``2_SCE.BIN`` bytes.
        replacements: ``{original_record_offset: complete_encoded_record}``.
            Each value must contain exactly one record and its FF terminator.
        strict_source: Require the known original size and SHA-256 by default.

    Returns:
        ``(rebuilt_bytes, manifest)``.  The function raises before returning if
        any pointer, record, script, alignment, or identity invariant fails.
    """
    source = bytes(source)
    normalised = _normalise_replacements(replacements)
    old_scenarios = _validate_source(source, strict_source)
    # Validate offsets before the lower-level relocation to give deterministic
    # production errors and to make an empty identity build explicit.
    known_offsets = _record_locations(old_scenarios)
    unknown = set(normalised) - set(known_offsets)
    if unknown:
        raise ValueError(
            "replacement offsets are not original record starts: "
            + ", ".join(f"{offset:#x}" for offset in sorted(unknown))
        )

    output = relocate_sce(source, normalised)
    new_scenarios, scenario_manifest, record_manifest = _verify_rebuild(
        source, output, normalised, old_scenarios
    )

    if not normalised and output != source:
        raise AssertionError("identity rebuild is not byte-for-byte identical")

    opcode_counts = {
        f"0x{opcode:02X}": sum(
            1
            for scenario in new_scenarios
            for reference in scenario.references
            if reference.opcode == opcode
        )
        for opcode in sorted(TEXT_POINTER_OPCODES)
    }
    manifest: dict[str, Any] = {
        "format": "srwcb-second-sce-expanded-v1",
        "source_size": len(source),
        "output_size": len(output),
        "file_byte_delta": len(output) - len(source),
        "source_sha256": sha256(source),
        "output_sha256": sha256(output),
        "strict_source_verified": strict_source,
        "identity_rebuild": not normalised,
        "replacement_count": len(normalised),
        "file_pointer_count": len(pointer_targets(output)),
        "scenario_count": len(new_scenarios),
        "record_count": sum(len(s.records) for s in new_scenarios),
        "direct_text_reference_count": sum(
            len(s.references) for s in new_scenarios
        ),
        "direct_text_reference_opcodes": opcode_counts,
        "all_scenario_blocks_aligned_4": all(
            s.block_start % 4 == 0 for s in new_scenarios
        ),
        "scenarios": scenario_manifest,
        "records": record_manifest,
    }
    return output, manifest


def run_self_test(source: bytes) -> dict[str, Any]:
    """Run an identity build and a cumulative, alignment-changing expansion."""
    identity, identity_manifest = rebuild_second_sce(source, {})
    if identity != source or not identity_manifest["identity_rebuild"]:
        raise AssertionError("identity self-test failed")

    scenarios = parse_scenarios(source)
    scenario = scenarios[1]
    # Expand two consecutive directly referenced records.  This verifies that
    # the second and all following B pointers accumulate prior record deltas.
    first = scenario.records[4]
    second = scenario.records[5]
    first_raw = source[first.start : first.end]
    second_raw = source[second.start : second.end]
    replacements = {
        first.start: first_raw[:-1] + b"\x00" * 3 + b"\xFF",
        second.start: second_raw[:-1] + b"\x00" * 4 + b"\xFF",
    }
    expanded, expanded_manifest = rebuild_second_sce(source, replacements)
    expanded_scenarios = parse_scenarios(expanded)
    if expanded_scenarios[2].block_start % 4:
        raise AssertionError("expanded self-test did not align the next block")
    if expanded_manifest["replacement_count"] != 2:
        raise AssertionError("expanded self-test replacement count mismatch")
    return {
        "identity": {
            "output_matches_source": identity == source,
            "output_size": len(identity),
            "output_sha256": sha256(identity),
        },
        "expanded": {
            "output_size": len(expanded),
            "file_byte_delta": len(expanded) - len(source),
            "replacement_count": expanded_manifest["replacement_count"],
            "scenario_1_pool_end": expanded_scenarios[1].pool_end,
            "scenario_2_block_start": expanded_scenarios[2].block_start,
            "all_blocks_aligned_4": expanded_manifest[
                "all_scenario_blocks_aligned_4"
            ],
            "direct_text_reference_count": expanded_manifest[
                "direct_text_reference_count"
            ],
            "output_sha256": sha256(expanded),
        },
    }


def _parse_offset(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a valid record offset")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"invalid record offset {value!r}")


def load_replacement_json(path: Path) -> dict[int, bytes]:
    """Load a small interchange manifest for command-line production use.

    Accepted forms are ``{"0x3A1B": "AA BB FF"}`` or
    ``{"records": [{"source_offset": "0x3A1B", "encoded_hex": "..."}]}``.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    replacements: dict[int, bytes] = {}
    if isinstance(document, dict) and "records" in document:
        rows = document["records"]
        if not isinstance(rows, list):
            raise ValueError("replacement JSON 'records' must be a list")
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("replacement JSON record must be an object")
            offset = _parse_offset(row["source_offset"])
            raw = bytes.fromhex(str(row["encoded_hex"]))
            if offset in replacements:
                raise ValueError(f"duplicate replacement offset {offset:#x}")
            replacements[offset] = raw
    elif isinstance(document, dict):
        for key, value in document.items():
            offset = _parse_offset(key)
            raw_hex = value.get("encoded_hex") if isinstance(value, dict) else value
            replacements[offset] = bytes.fromhex(str(raw_hex))
    else:
        raise ValueError("replacement JSON must be an object")
    return replacements


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="pristine SECOND/2_SCE.BIN")
    parser.add_argument("--replacements", type=Path, help="replacement JSON")
    parser.add_argument("--output", type=Path, help="rebuilt 2_SCE.BIN")
    parser.add_argument("--report", type=Path, help="UTF-8 JSON verification report")
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run identity and cumulative-expansion tests without writing an archive",
    )
    args = parser.parse_args()
    source = args.source.read_bytes()
    if args.self_test:
        result = run_self_test(source)
        rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    if args.replacements is None or args.output is None:
        parser.error("normal builds require --replacements and --output")
    replacements = load_replacement_json(args.replacements)
    output, manifest = rebuild_second_sce(source, replacements)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(output)
    rendered = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
