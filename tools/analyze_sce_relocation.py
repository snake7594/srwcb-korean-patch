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
from dataclasses import dataclass, field
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
TEXT_POINTER_OPCODES = frozenset((0xB1, 0xB2, 0xB3, 0xB4))
#: ``<옵코드> <변위16>`` 형태 — 피연산자가 옵코드+1 이다.
#:
#: `B2` 는 2026-08-19 제보 #12·#15·#17 을 파다가 뒤늦게 찾았다. 레트일 세 게임
#: 전수로 **풀 안을 겨누는 18곳 전부가 레코드 시작**(18/18)이고, 레코드 시작을
#: 겨눈 사이트 29곳(제3차 9·EX 19·제2차 1)은 뒤따르는 바이트가 `<u16> 0A 01`
#: (선택지) 또는 `<u16> 62 00`(일반 대사)로 일관된다. 전 바이트 기준 적중률만
#: 보면 0.95% 로 기준선(0.8%)과 비슷하지만, 재조준기는 '레트일에서 레코드 시작을
#: 정확히 겨눈 자리'만 건드리므로 오탐이 바이트를 망가뜨리지 않는다.
#: 이게 빠져 있어서 EX 마사키편 1화 ISS 설명 12줄이 통째로 엉뚱한 대사를 띄웠고
#: (i12_2), 제3차 22~23화 북아메리카/아프리카 선택지가 깨졌다(i17_4).
#: ``<옵코드> <1바이트 인자> <변위16>`` 형태 — 피연산자가 옵코드+2 다.
#:
#: 레트일 세 게임의 시나리오를 전수 조사해 골랐다(2026-08-12). 판정 기준은
#: ① 변위가 레코드 시작을 가리키는 비율이 무작위 기댓값(0.8%)의 수십 배이고
#: ② 이미 인식되는 포인터와 **같은 자리가 아니며**(그림자가 아니고)
#: ③ 표본을 열어 보면 실제 대사 레코드를 가리킨다.
#:
#:     B9 03  367/372(99%)   B9 08  12/13   B6 01  8/14   B6 00  336/…
#:
#: `B0 FF`(29곳)·`BE 01`(10곳)은 탈락시켰다 — 문맥 바이트가 전부 똑같고 일정
#: 간격으로 반복되는 **데이터**라 우연히 맞은 것이었다(표본 전부 같은 대사).
ARG_POINTER_FORMS = frozenset((
    (0xB6, 0x00), (0xB6, 0x01),
    (0xB9, 0x03), (0xB9, 0x08),
))
# 작전목적 앞머리 블록의 최대 레코드 수 (실측 4~11)
OBJECTIVE_BLOCK_MAX = 12
# 대사 인용부호 「 의 글리프 바이트 — 앞머리 블록은 여기서 끊는다
_DIALOGUE_QUOTE = 0x3E
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


# 재배치 후 우연히 참조 조건을 만족하게 된 위치(런타임 무해, 진단용 기록)
SPURIOUS_REFERENCE_GAINS: list[dict] = []


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
        # EX scenario 68 ends its pool with an unterminated run ("COMING SOON /
        # NEXT / 第4次スーパーロボット大戦") that runs exactly to pool_end with no
        # FF.  It carries no further record, so leave it in the pool tail, which
        # the rebuilder preserves and verifies byte-for-byte.
        unterminated_trailing_run = 0xFF not in tail
        if not unterminated_trailing_run and (len(tail) > 3 or any(tail)):
            raise ValueError(f"unterminated record at {record_start:#x}")
    return tuple(records)


def iter_pointer_sites(data: bytes, start: int, end: int):
    """(옵코드 위치, 피연산자 위치, 옵코드) 후보를 훑는다.

    대사 포인터 명령은 두 가지 형태다 (THIRD.WAR 역어셈블로 확인, 2026-08-09):

    * ``B1/B3/B4 <변위16>``      — 피연산자가 옵코드 바로 뒤.
    * ``B6 00 <변위16>`` 등      — 핸들러(점프표 0x1F, 0x800CB1D0)가 VM 커서를
      **1 늘린 뒤** 공통 루틴 0x800CB290 을 부른다. 그래서 피연산자가 옵코드+2.
      이걸 놓치면 그 대사들만 원문 자리에 남아, 레코드를 옮긴 만큼 밀려서
      나온다(제3차 8화·10화의 '화자가 사라지고 문장 중간부터' 증상).
      같은 꼴이 ``B6 01`` / ``B9 03`` / ``B9 08`` 에도 있다 — ``B9 03`` 만
      세 게임 합쳐 367곳이라, 12~16화의 대사가 통째로 밀렸다(2026-08-12 제보 #8).

    변위는 **부호 있는 16비트**다 (0x800CB2F4: sll 16 / sra 16).

    ## `F0 00 <변위16>` 안쪽은 건너뛴다 (2026-08-21)

    이벤트 스크립트에는 `F0 00 <변위16>` 4바이트 명령(공통 서브루틴 호출)이 아주
    많다. 그 **변위 바이트가 우연히 포인터 옵코드처럼 보이면**, 여기서 그 자리를
    옵코드로 인정해 버리고 재조준기가 옵코드+2(= **다음 명령**)를 변위로 덮어쓴다.

        레트일  f0 00 b6 00 | b9 02 63 7a      <- `b6 00` 은 F0 의 변위
        우리    f0 00 b6 00 | 73 03 63 7a      <- B9 02 옵코드가 사라졌다

    레트일 변위가 우연히 레코드 시작을 정확히 겨누면 재조준기의 필터도 통과한다.
    EX sc23 이 그 경우였고, 볼크루스 전투 뒤 스크립트가 통째로 탈선해 인터프리터가
    빈 힙을 기어다녔다(커서 소실·입력 무반응·음악은 계속). 세이브스테이트에 심은
    제어흐름 추적으로 확정했다. 전수 조사 결과 EX 6곳 · 제3차 2곳 · 제2차 0곳.

    그래서 `F0 00` 뒤 2바이트(변위 자리)에서 시작하는 후보는 인정하지 않는다.
    """
    f0_operand = bytearray(end - start)
    off = start
    while off < end - 3:
        if data[off] == 0xF0 and data[off + 1] == 0x00:
            for k in (2, 3):
                if off + k < end:
                    f0_operand[off + k - start] = 1
            off += 4
        else:
            off += 1

    for off in range(start, end - 2):
        if f0_operand[off - start]:
            continue                      # `F0 00 <변위16>` 의 변위 자리
        op = data[off]
        if op in TEXT_POINTER_OPCODES:
            yield off, off + 1, op
        elif off + 3 < end and (op, data[off + 1]) in ARG_POINTER_FORMS:
            yield off, off + 2, op


def find_text_references(
    data: bytes, block_start: int, pool_start: int, records: tuple[Record, ...]
) -> tuple[TextReference, ...]:
    """레코드 시작을 가리키는 VM 대사 포인터를 모두 찾는다."""
    starts = {record.start for record in records}
    references: list[TextReference] = []
    for opcode_offset, operand_offset, opcode in iter_pointer_sites(
        data, block_start, pool_start
    ):
        displacement = struct.unpack_from("<h", data, operand_offset)[0]
        target = operand_offset + displacement
        if target in starts:
            references.append(
                TextReference(opcode_offset, operand_offset, target, opcode)
            )
    return tuple(references)


def objective_block_records(data: bytes) -> set[int]:
    """작전목적(승리/패배조건) 블록에 속한 레코드 시작 오프셋.

    시나리오 앞머리에는 대사 포인터가 겨누지 않는 레코드 몇 개가 모여 있는데,
    이게 작전목적 화면이 읽는 조건문·목표 텍스트다(제3차 기준 시나리오당 4~11개).
    **여기에 줄(F6)을 하나라도 더 넣으면 작전목적 창이 깨지고 게임이 멈춘다**
    (2026-08-09 제보, 8화). 그래서 이 레코드들은 원문이 쓴 줄 수를 지켜야 한다.

    첫 대사 포인터가 겨누는 레코드 앞까지를 그 블록으로 본다. 다만 대사 포인터가
    한참 뒤에야 나오는 시나리오(튜토리얼 등)에서는 이 규칙이 진짜 대사까지 삼켜
    버리므로 ``OBJECTIVE_BLOCK_MAX`` 로 자른다. 실제 앞머리는 4~11개다.
    """
    out: set[int] = set()
    for scenario in parse_scenarios(data):
        targets = set()
        for _off, operand, _op in iter_pointer_sites(
            data, scenario.block_start, scenario.record_data_end
        ):
            targets.add(operand + struct.unpack_from("<h", data, operand)[0])
        for index, record in enumerate(scenario.records):
            if record.start in targets or index >= OBJECTIVE_BLOCK_MAX:
                break
            # 대사(「…」)가 나오면 거기서 앞머리 블록은 끝이다. 튜토리얼처럼 대사가
            # 일찍 시작하는 시나리오에서 진짜 대사까지 못박아 잘리는 걸 막는다.
            if _DIALOGUE_QUOTE in data[record.start:record.end]:
                break
            out.add(record.start)
    return out


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
            # 변위는 부호 있는 16비트다 (엔진이 sll 16 / sra 16 으로 확장한다).
            if not -0x8000 <= displacement <= 0x7FFF:
                raise ValueError(
                    f"scenario {scenario.index} text reference at "
                    f"{reference.operand_offset:#x} exceeds s16 after relocation: "
                    f"{displacement:#x}"
                )
            struct.pack_into("<h", script, operand_rel, displacement)

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
        old_ops = {r.operand_offset - old.block_start: r.opcode for r in old.references}
        new_ops = {r.operand_offset - new.block_start: r.opcode for r in new.references}
        lost = sorted(set(old_ops) - set(new_ops))
        gained = sorted(set(new_ops) - set(old_ops))
        # 유실은 치명적(실제 대사 포인터가 끊긴 것). 추가는 탐지기 오탐일 수 있다:
        # find_text_references는 "B1/B3/B4 바이트 + u16 피연산자가 레코드 시작을 가리킴"이라는
        # 휴리스틱이라, 레코드가 이동하면 무관한 바이트열이 우연히 조건을 만족할 수 있다.
        # 그런 위치는 피연산자가 재기록되지 않았으므로(참조 집합 밖) 런타임 동작은 불변.
        if lost:
            raise AssertionError(
                f"scenario {old.index} lost text references after relocation: "
                f"{len(old.references)} -> {len(new.references)}; "
                f"lost(block-rel)={[hex(x) for x in lost[:8]]} "
                f"opcodes={[hex(old_ops[x]) for x in lost[:8]]}"
            )
        if gained:
            SPURIOUS_REFERENCE_GAINS.append(
                {"scenario": old.index, "block_relative": gained,
                 "opcodes": [new_ops[x] for x in gained]}
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


# --------------------------------------------------------------------------
# 레코드 '안쪽 메시지 시작' 앵커
#
# FF 종단 레코드 하나에 `[F9 02]○○페이즈. 마사키「…」` 처럼 시스템문과 대사가
# 붙어 있고, VM 이 그 **안쪽** 바이트를 직접 겨누는 포인터가 있다(제3차 12,
# EX 45곳). 레코드를 쪼개면 번역 원장 키가 깨지므로, 배포본 레코드 안에서
# **같은 메시지 경계를 다시 찾아** 변위만 고친다.
# --------------------------------------------------------------------------
#: 대사 인용부호 「 의 글리프 인덱스(1바이트). 레트일·한글 폰트 모두 같은 값이다.
QUOTE_OPEN = 0x3E
#: 화자 이름에는 절대 들어가지 않는 글리프 — 이름을 거꾸로 훑을 때 여기서 멈춘다.
#: 반각공백 0x00 / ? 0x14 / ! 0x15 / , 0x3A / . 0x3B / 「 0x3E / 」 0x3F / 。 0xE4
NAME_STOP_GLYPHS = frozenset((0x00, 0x14, 0x15, 0x3A, 0x3B, 0x3E, 0x3F, 0xE4))
#: 화자 이름 최대 글리프 수 (레트일 최장 ベルト-チカ 6, 여유 있게)
NAME_MAX_GLYPHS = 10
#: 산출 앵커의 레코드 안 상대 위치가 레트일과 이만큼 넘게 다르면 버린다
#: (실측 57곳의 최대 편차 0.107 → 0.25 는 두 배 이상 여유).
ANCHOR_RATIO_TOLERANCE = 0.25
#: 자동 산출이 실패한 곳만 손으로 적는 폴백.
#: 키 = (게임 파일이름, 레트일 옵코드 절대오프셋)
#: 값 = (KO 레코드 안 몇 번째 「 인가, 그 「 앞 화자 이름의 글리프 개수)
#: 2026-08-19 실측으로는 **비어 있다**(57/57 자동 해결).
ANCHOR_OVERRIDES: dict = {}


def tokenize_record(data, start: int, end: int):
    """레코드를 토큰으로 쪼갠다 -> [(오프셋, 종류, 값)].

    종류: 'g' 글리프(값=글리프 인덱스) / 'c' 제어(값=옵코드) / 'e' 0xFF.
    """
    out = []
    p = start
    while p < end:
        b = data[p]
        if b < 0xEB:
            out.append((p, 'g', b)); p += 1
        elif b < 0xF6:
            if p + 1 >= end:
                break
            out.append((p, 'g', ((b - 0xEB) << 8) | data[p + 1])); p += 2
        elif b == 0xFF:
            out.append((p, 'e', b)); p += 1
        else:
            out.append((p, 'c', b)); p += 1 + CONTROL_ARG_LENGTHS[b]
    return out


def _speaker_from(tokens, i: int):
    """tokens[i:] 가 `이름「` 꼴로 시작하면 (이름 글리프 튜플, 「 토큰번호)."""
    name = []
    j = i
    while j < len(tokens) and len(name) <= NAME_MAX_GLYPHS:
        _off, kind, idx = tokens[j]
        if kind != 'g':
            return None
        if idx == QUOTE_OPEN:
            return (tuple(name), j) if name else None
        if idx in NAME_STOP_GLYPHS:
            return None
        name.append(idx)
        j += 1
    return None


@dataclass
class AnchorContext:
    """레트일-배포본 화자 이름 사전. 파일 한 쌍마다 한 번만 만든다."""
    jp_names: set = field(default_factory=set)      # 레트일 화자 이름(글리프 튜플)
    jp_to_ko: dict = field(default_factory=dict)    # 레트일 이름 -> {한글 이름: 빈도}
    ko_names: set = field(default_factory=set)      # 한글 화자 이름 전체


def build_anchor_context(jp: bytes, ko: bytes, jp_scenarios, ko_scenarios):
    """`이름「` 로 **시작하는 레코드**에서 화자 이름 사전을 뽑는다.

    레코드 시작 대사는 게임마다 수천 개라 이름 목록도 JP->KO 대응도 여기서
    전부 나온다(제3차 175/168, EX 137/133, 제2차 97/87). 외부 이름 사전에
    기대지 않으므로 번역이 바뀌어도 저절로 따라간다.
    """
    ctx = AnchorContext()
    for s in jp_scenarios:
        for r in s.records:
            sp = _speaker_from(tokenize_record(jp, r.start, r.end), 0)
            if sp:
                ctx.jp_names.add(sp[0])
    for sj, sk in zip(jp_scenarios, ko_scenarios):
        if len(sj.records) != len(sk.records):
            continue
        for rj, rk in zip(sj.records, sk.records):
            a = _speaker_from(tokenize_record(jp, rj.start, rj.end), 0)
            b = _speaker_from(tokenize_record(ko, rk.start, rk.end), 0)
            if b:
                ctx.ko_names.add(b[0])
            if a and b:
                ctx.jp_to_ko.setdefault(a[0], {})
                ctx.jp_to_ko[a[0]][b[0]] = ctx.jp_to_ko[a[0]].get(b[0], 0) + 1
    return ctx


def jp_message_anchor(jp: bytes, record, target: int, ctx: AnchorContext):
    """레트일 포인터가 레코드 **안쪽 메시지 시작**을 겨누는가?

    맞으면 {'name','quote_ordinal','ratio','rel'}, 아니면 None.

    판정 기준은 두 가지뿐이고 둘 다 파일에서 스스로 뽑은 것이다.
      (1) 목표가 토큰 경계이고, 거기서부터 `이름「` 꼴이 이어진다.
      (2) 그 이름이 **레코드 시작 대사에서 실제로 쓰인 화자 이름**이다.
    (2)가 없으면 이름 한복판(`ェンドロ「`, `-ネ「`, `ュウ「`)을 우연히 맞힌
    잡음이 섞인다 — 레트일 실측 356 후보 중 (1)이 291곳, (2)가 다시 8곳을
    걸러 내고 57곳만 남는다.
    """
    if not (record.start < target < record.end):
        return None
    tokens = tokenize_record(jp, record.start, record.end)
    index = {t[0]: i for i, t in enumerate(tokens)}
    i = index.get(target)
    if i is None:
        return None
    sp = _speaker_from(tokens, i)
    if sp is None or sp[0] not in ctx.jp_names:
        return None
    span = record.end - record.start
    return {
        "name": sp[0],
        "quote_ordinal": sum(1 for t in tokens[:i]
                             if t[1] == 'g' and t[2] == QUOTE_OPEN),
        "ratio": (target - record.start) / span if span else 0.0,
        "rel": target - record.start,
    }


def _name_start_before(tokens, quote_index: int, want_ko: dict, ko_names: set):
    """KO 레코드에서 「 앞 화자 이름의 **시작 오프셋**을 고른다 -> (오프셋, 방법)."""
    back = []
    j = quote_index - 1
    while j >= 0 and len(back) < NAME_MAX_GLYPHS + 4:
        _off, kind, idx = tokens[j]
        if kind != 'g' or idx in NAME_STOP_GLYPHS:
            break
        back.append(j)
        j -= 1
    if not back:
        return None, None
    back.reverse()
    seq = tuple(tokens[j][2] for j in back)
    for cand, _n in sorted(want_ko.items(), key=lambda kv: (-kv[1], -len(kv[0]))):
        if 0 < len(cand) <= len(seq) and seq[len(seq) - len(cand):] == cand:
            return tokens[back[len(seq) - len(cand)]][0], "namemap"
    for length in range(min(len(seq), NAME_MAX_GLYPHS), 0, -1):
        if seq[len(seq) - length:] in ko_names:
            return tokens[back[len(seq) - length]][0], "lexicon"
    return tokens[back[0]][0], "delimiter"


_ANCHOR_HOW_RANK = {"namemap": 0, "lexicon": 1, "delimiter": 2}


def ko_message_anchor(ko: bytes, record, info: dict, ctx: AnchorContext,
                      override=None):
    """배포본 레코드에서 같은 메시지 시작 오프셋을 찾는다 -> (오프셋, 방법).

    실패하면 (record.start, 'record-start') — 레코드를 통째로 그리게 두는 편이
    레트일 변위를 남겨 **엉뚱한 레코드 한복판**을 찌르는 것보다 안전하다.
    """
    tokens = tokenize_record(ko, record.start, record.end)
    quotes = [i for i, t in enumerate(tokens)
              if t[1] == 'g' and t[2] == QUOTE_OPEN]
    if not quotes:
        return record.start, "record-start"
    if override is not None:
        k, back = override
        if k < len(quotes) and quotes[k] - back >= 0:
            return tokens[quotes[k] - back][0], "override"
    span = record.end - record.start
    want_ko = ctx.jp_to_ko.get(info["name"], {})
    best = None
    for ordinal, qi in enumerate(quotes):
        off, how = _name_start_before(tokens, qi, want_ko, ctx.ko_names)
        if off is None:
            continue
        delta = abs((off - record.start) / span - info["ratio"]) if span else 0.0
        if delta > ANCHOR_RATIO_TOLERANCE:
            continue
        score = (_ANCHOR_HOW_RANK[how],
                 0 if ordinal == info["quote_ordinal"] else 1, delta)
        if best is None or score < best[0]:
            best = (score, off, how)
    if best is None:
        return record.start, "record-start"
    return best[1], best[2]


def resolve_inner_anchor(jp: bytes, ko: bytes, jp_record, ko_record, target: int,
                         ctx: AnchorContext, game: str = "", opcode_offset: int = -1):
    """레트일 앵커 -> 배포본 앵커. 앵커가 아니면 None.

    `second-fixes/fix_sce_event_refs.py` 가 '목표가 레코드 시작이 아닐 때'
    이 함수를 부른다.
    """
    info = jp_message_anchor(jp, jp_record, target, ctx)
    if info is None:
        return None
    override = ANCHOR_OVERRIDES.get((game, opcode_offset))
    off, how = ko_message_anchor(ko, ko_record, info, ctx, override)
    return {"offset": off, "how": how, "info": info}


if __name__ == "__main__":
    raise SystemExit(main())
