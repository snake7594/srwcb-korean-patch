#!/usr/bin/env python3
"""Repack SECOND.WAR's Korean menu resources inside the retail image.

SECOND uses fixed work buffers throughout the RAM that follows its BSS.  A
previous implementation extended the PS-X EXE into that address space, which
let normal map/menu rendering overwrite translated strings.  This module
keeps the retail executable size and memory map, reuses only guarded original
record spans, and rewrites the proven self-relative pointer fields.

The patcher is intentionally fail-closed.  Every source record is checked by
SHA-256 (and, when supplied, exact bytes), replacement spans may cover glyph
bytes only, renderer controls must remain unchanged, and no text is shortened
or truncated to fit an old slot.
"""

from __future__ import annotations

import hashlib
import json
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

try:
    from .second_ui_phase_compaction import (
        FIXED_UI_PHASE_COMPACTION_BY_ASSET_SOURCE,
    )
except ImportError:  # Direct script execution keeps tools/ on sys.path.
    from second_ui_phase_compaction import FIXED_UI_PHASE_COMPACTION_BY_ASSET_SOURCE

try:
    from .second_translation_codec import assemble_translated_record
except ImportError:  # Direct script execution keeps tools/ on sys.path.
    from second_translation_codec import assemble_translated_record


PSX_EXE_MAGIC = b"PS-X EXE"
PSX_HEADER_BYTES = 0x800
PSX_FILE_TO_RAM_BIAS = 0x8000F800
RETAIL_FILE_BYTES = 0x12B000
RETAIL_T_SIZE = 0x12A800

# The 91-record preview/conditions pool is the tail of root VM script 3.  It
# has no leaf pointers, so the complete script is copied to the arena and the
# sole authoritative root-table field is redirected.  The original block is
# deliberately retained for any non-authoritative incidental readers.
ROOT_RESOURCE_HEADER = 0xE14
ROOT_SCRIPT_ENTRY3_FIELD = 0xE24
ROOT_SCRIPT_ENTRY3_START = 0x1231
ROOT_SCRIPT_ENTRY3_END = 0x6A6C
ROOT_SCRIPT_ENTRY3_SHA256 = "9a339054071a07842f98462be5b62e9189f9b3e8b13c09b958627fe3624e1ca5"

# Moving the four-entry root header and its three shared prefix entry points
# frees 0xE14..0x1230.  Script 3 can then grow backwards while still ending
# before the untouched following resource at 0x6A6C.  The destination is 33
# consecutive font slots (0xA47..0xA67) that are absent from every final UI
# record; the builder additionally rejects a glyph map that assigns them.
ROOT_RELOCATION_CAVE_START = 0x3C938
ROOT_RELOCATION_CAVE_END = 0x3CD58
ROOT_PREFIX_BLOCK_START = ROOT_RESOURCE_HEADER
ROOT_PREFIX_BLOCK_END = ROOT_SCRIPT_ENTRY3_START
ROOT_PREFIX_BLOCK_SHA256 = "f51f9590971f0b1a92c673c7a795b40003d44f8f2693e6ed2e0172fe326a46ef"
ROOT_RELOCATION_CAVE_SHA256 = "da37de2fb3c1e6bf07d2323f448fc9e578f0e9aba2e699fe734e66de546cbadd"
ROOT_HEADER_LOAD_SITES = (
    (0x48160, 4),  # a0
    (0x48184, 4),  # a0
    (0x59928, 2),  # v0
    (0x59FC0, 2),  # v0
)

# The final dynamic font currently assigns glyphs only through 0xA2F.  Keep
# the root-header cave above separate, then use the still-unassigned 0xA68..
# 0xAFF tail as guarded static storage.  This is deliberately conditional on
# both the pristine bytes and the final glyph map: a future translation that
# needs one of these slots must fail instead of silently corrupting its font.
STATIC_FONT_DONOR_GLYPH_START = 0xA68
STATIC_FONT_DONOR_GLYPH_END = 0xB00
STATIC_FONT_DONOR_START = 0x3CD58
STATIC_FONT_DONOR_END = 0x3E058
STATIC_FONT_DONOR_SHA256 = "d5993c29f25d93133c3f4e2a3b65a7a727282f6abc5d3ebc8538fa550a0b44c1"
# One final preview record retains source glyph 0xAA7 outside its translated
# span.  Two preserved, unreferenced BMESS2 records also contain 0xAFA/0xAFB.
# Leave all three pixels intact; the latter two are not live runtime text, but
# retaining them keeps the cross-archive font-tail claim conservative.
STATIC_FONT_DONOR_EXCLUDED_GLYPHS = (0xAA7, 0xAFA, 0xAFB)

# Common master entry 23 is consumed by the settings VM in exact four-byte
# groups (``F8 04``).  Widening only the producer makes the following four-byte
# values consume the second half of the sound label and shifts every later
# setting.  Keep the retail instructions intact; the Korean labels themselves
# are compacted to two full-width glyphs / four encoded bytes.
COMMON_AUDIO_OPTION_WIDTH_PATCHES: tuple[tuple[int, bytes, bytes], ...] = ()

# These common save prompts live in a small renderer-record block outside the
# pointer-backed menu master.  They are byte-identical in the shared front-end
# and SECOND.WAR, so patch them in place while preserving each retail record's
# byte length (zero is the renderer's true space glyph).
COMMON_SAVE_PROMPT_RECORDS = (
    (
        "save_confirm",
        bytes.fromhex(
            "AA 11 C5 56 7D 58 E4 87 8C 56 43 66 58 4A 14 FF"
        ),
        "저장할까요?",
    ),
    (
        "save_complete_continue",
        bytes.fromhex(
            "AA 11 C5 EC E4 EC E5 56 7D 56 5E E4 A1 11 CF 8E "
            "EE 16 50 7D 58 4A 14 FF"
        ),
        "저장 완료! 계속할까요?",
    ),
)


def patch_map_label_heap(
    executable: bytearray,
    overlay_path: Path,
    glyph_map: dict[str, int],
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    """Byte-exact in-place Korean for the sequential map-UI label heap.

    The 0x9D34..0x9A4FF label/message heap has no discoverable per-record
    pointers (no absolute words, no lui/addiu pairs, no s32/u16 tables), so it
    is assumed index-addressed by sequential FF walking.  Every record is
    therefore rewritten inside its exact retail byte extent: the Korean
    encoding must fit the original content size, the remainder is padded with
    the renderer's true 0x00 space glyph, and the FF terminator never moves.
    """

    document = load_json(overlay_path)
    if document.get("schema") != "srwcb-second-map-label-overlay-v1":
        raise ValueError(f"unexpected map-label overlay schema in {overlay_path}")
    spans: list[tuple[int, int]] = []
    records: list[dict[str, Any]] = []
    translated = 0
    for row in document.get("records", []):
        offset = _int(row["offset"], "offset")
        budget = _int(row["budget_bytes"], "budget_bytes")
        source = bytes.fromhex(str(row["source_hex"]))
        if len(source) != budget:
            raise ValueError(f"map label {offset:#x}: source/budget mismatch")
        if bytes(executable[offset:offset + budget]) != source:
            raise ValueError(f"map label source changed at {offset:#x}")
        if executable[offset + budget] != 0xFF:
            raise ValueError(f"map label terminator missing at {offset:#x}")
        korean = str(row.get("korean_text") or "")
        if not korean:
            continue
        encoded = encode_ui_text(
            korean.replace("[F6]", "\n"), glyph_map, terminate=False
        )
        if len(encoded) > budget:
            raise ValueError(
                f"map label {offset:#x} needs {len(encoded)} bytes; "
                f"retail record is {budget}"
            )
        executable[offset:offset + budget] = encoded + b"\x00" * (
            budget - len(encoded)
        )
        spans.append((offset, offset + budget))
        translated += 1
        records.append(
            {
                "offset": offset,
                "budget_bytes": budget,
                "encoded_bytes": len(encoded),
                "korean_text": korean,
            }
        )
    return spans, {
        "asset_id": "second_map_label_heap",
        "record_count": len(document.get("records", [])),
        "translated_records": translated,
        "storage": "byte-exact in-place records; FF terminators preserved",
        "records": records,
    }


def _patch_common_save_prompt_records(
    executable: bytearray,
    glyph_map: dict[str, int],
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    spans: list[tuple[int, int]] = []
    rows: list[dict[str, Any]] = []
    for key, source, korean in COMMON_SAVE_PROMPT_RECORDS:
        replacement_text = encode_ui_text(korean, glyph_map, terminate=False)
        if len(replacement_text) > len(source) - 1:
            raise ValueError(f"common save prompt {key} exceeds its source record")
        replacement = (
            replacement_text
            + bytes((0x00,)) * (len(source) - 1 - len(replacement_text))
            + b"\xFF"
        )
        if len(replacement) != len(source):
            raise AssertionError("common save prompt replacement changed record size")
        cursor = 0
        hits = 0
        while True:
            offset = bytes(executable).find(source, cursor)
            if offset < 0:
                break
            executable[offset:offset + len(source)] = replacement
            spans.append((offset, offset + len(source)))
            rows.append(
                {
                    "key": key,
                    "file_offset": offset,
                    "source_size": len(source),
                    "korean_text": korean,
                }
            )
            hits += 1
            cursor = offset + len(source)
        if key == "save_confirm" and hits == 0:
            raise ValueError("shared/SECOND save-confirm record was not found")
    return spans, {
        "asset_id": "common_save_prompt_records",
        "patched_records": rows,
        "patched_record_count": len(rows),
    }

SECOND_EXECUTABLE = Path("SECOND/SECOND.WAR")

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

# SECOND's pointer-backed UI master is a stateful VM stream, not an ordinary
# dialogue-renderer string.  F7 owns two argument bytes here and switches the
# following stream to extended-font mode when bit 0x40 is set in its big-endian
# argument word.  Preview/dialogue records keep CONTROL_ARG_LENGTHS above,
# where F7 remains a zero-argument page break.
SECOND_UI_VM_COMMON_ARG_LENGTHS: dict[int, int] = {
    0xF6: 0,
    0xF7: 2,
    0xF8: 1,
    0xF9: 1,
    0xFA: 0,
    0xFB: 2,
    0xFC: 2,
    0xFD: 2,
    0xFE: 1,
}

SECOND_UI_VM_COMPACT_ARG_LENGTHS: dict[int, int] = {
    0xF0: 3,
    0xF1: 2,
    0xF2: 3,
    0xF3: 3,
    0xF4: 1,
    0xF5: 3,
}

RENDERER_GRAMMAR = "renderer"
SECOND_UI_VM_GRAMMAR = "second_ui_vm"

# Retail glyph 0x3FF is an intentionally empty *high* glyph.  Unlike a normal
# 0x00 space it advances/toggles the wide-glyph phase, so it can preserve the
# renderer state of Japanese mixed-width labels without drawing anything.
# build_second_expanded_patch restores its pristine bitmap before injection.
RENDERER_HIGH_BLANK = bytes.fromhex("EE FF")

JAPANESE_OR_HAN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")

CHAR_NORMALISATION = {
    "\u00a0": " ",
    "\u3000": " ",
    "\t": " ",
    "，": ",",
    "．": ".",
    "？": "?",
    "！": "!",
    "：": ":",
    "；": ";",
    "（": "(",
    "）": ")",
    "［": "[",
    "］": "]",
    "【": "[",
    "】": "]",
    "‘": "'",
    "’": "'",
    "·": "・",
    "ㆍ": "・",
    "~": "〜",
    "～": "〜",
    "—": "-",
    "–": "-",
    "―": "-",
    "−": "-",
}

# A handful of UI-master rows were marked non-target in the first inventory,
# and several shared SLPS rows use a slightly different byte layout.  These
# source-text keyed fallbacks complete those rows without relying on offsets
# from another executable.  The normal reviewed overlay still takes
# precedence; this map is consulted only for uncovered glyph runs.
COMPLETE_UI_TEXT_TRANSLATIONS = {
    "マップをみる": "지도 보기",
    "出撃": "출격",
    "レベル": "레벨",
    "ユニット": "유닛",
    "の全滅": " 전멸",
    "味方の全滅": "아군 전멸",
    "スピドアップ": "속도 올리기",
    "スピ-ドアップ": "속도 올리기",
    "をうける": "을 받는",
    "は": "은",
    "の": "의",
    "終了": "종료",
    "セ-ブ": "저장",
    "タ-ン数": "턴 수",
    "資金": "자금",
    "デ-タ": "데이터",
    "総タ-ン数": "총 턴 수",
    "第": "제",
    "話「": "화「",
    "本体RAM": "본체RAM",
    "カ-トリッジRAM": "카트리지 RAM",
    "マニュアル": "수동",
    "積極的に!": "적극적으로!",
    "効率よく!": "효율적으로!",
    "反撃するな!": "반격하지 마!",
    "どの章から始めますか?": "어느 장부터 시작합니까?",
    "マサキの章": "마사키의 장",
    "マサキの": "마사키의 ",
    "難度": "난이도",
    "やさしい": "쉬움",
    "リュ-ネの章": "류네의 장",
    "リュネの": "류네의 ",
    "ふつう": "보통",
    "シュウの章": "슈우의 장",
    "シュウの": "슈우의 ",
    "むずかしい": "어려움",
    "をいますか": "를 사용합니까",
    "ISSを使いますか?": "ISS를 사용합니까?",
    "何それ?": "그게 뭐죠?",
    "マサキの章 難度 やさしい": "마사키의 장  난이도 쉬움",
    "リュ-ネの章難度 ふつう": "류네의 장 난이도 보통",
    "マサキの章 ISSを使いますか?": "마사키의 장 ISS를 사용합니까?",
    "リュ-ネの章難度 ふ": "류네의 장 난이도 보통",
    "シュウの章 難度 むずかしい": "슈우의 장  난이도 어려움",
    "シュウの章 難度 む": "슈우의 장 난이도 어려움",
    "はい": "예",
    "いいえ": "아니요",
    "ロ-ド": "불러오기",
    "コンティニュ-": "이어하기",
    "スタ-ト": "시작",
    "オプション": "옵션",
    "デモセレクト": "데모 선택",
    "カラオケモ-ド": "가라오케 모드",
}

# Japanese menu fields reserve a fixed number of renderer cells.  Korean is
# semantically clearer when it retains spaces, but those spaces cannot be
# allowed to push into the next column.  These reviewed short forms are used
# only in fixed UI fields; dialogue and table text keeps its full translation.
UI_DISPLAY_COMPACTION = {
    "空": "공",
    "陸": "육",
    "海": "해",
    "宇": "우",
    "修理費用": "수리비용",
    "特殊技能": "특수기능",
    "射程": "사정",
    "弾数": "탄수",
    "地形　空": "지형 공",
    "必要気力": "필요기력",
    "消費EN": "EN소비",
    "必要技能": "필요기능",
    "精神": "정신",
    "部隊表": "부대표",
    "反撃命令": "반격명령",
    "作戦目的": "작전목적",
    "精神検索": "정신검색",
    "出撃ユニット選択　あと": "출격 유닛 남은",
    "行動終了していないユニットが　　体あります": "미행동 유닛이　　기 남음",
    "よろしいですか?": "확인할까요?",
    "しますか?": "할까요?",
    "勝利条件": "승리조건",
    "敗北条件": "패배조건",
    "武器改造": "무기개조",
    "」までクリア": "」까지완료",
    "全員の命令を一吝変更": "전원명령일괄변경",
    "積極的に!": "적극!",
    "効率よく!": "효율적!",
    "反撃するな!": "반격 금지!",
    "精神検索一覧": "정신 검색",
    "消費精神ポイント": "정신소비",
    "身代わり": "대신",
    ":アニメ:": ":애니:",
    "EN攻消費": "공격EN",
    "EN防消費": "방어EN",
    "HP吸収": "HP흡수",
    "ツバゼリ": "칼날",
    "盾防": "방어",
    "スクエア": "이동범위",
    "戦闘BGM設定": "전투BGM",
    "特殊操作": "특수조작",
    "初期設定に戻す": "초기화",
    "これでいい": "확정",
    "主人公設定の変更": "주인공설정변경",
    "主人公設定": "주인공설정",
    "顔": "얼",
    "性別　男": "남자",
    "性別　女": "여자",
    "名前と愛称を入力してください。": "이름·애칭을 입력하세요.",
    "残りの精神ポイント×2": "남은 정신×2",
    "どの能力を改造しますか?": "개조할 능력은?",
    "空.陸.海.宇のいずれかの地形適応をAにできる。": "공·육·해·우 중 하나를 A로",
    "の能力を表示する。": "능력 표시",
    "地形適応の": "지형 적응",
    "Aにします。": "A로 변경",
    "ロ-ド": "로드",
    "本体RAM": "본체RAM",
    "話までクリア": "화까지완료",
    "制限をうける能力　　　()内は本来の能力": "제한 능력 ( )",
    "のせかえます。　よろしいですか?": "탑승할까요?",
    "つきます。資金(": "보너스 자금(",
    ")を投入する事で以下の武器が付加": ")투입 시 무기 추가",
    "されます。投入しますか?": "추가할까요?",
    "装弾数": "장탄수",
    "装備中のパ-ツ": "장착 파츠",
    "強化パ-ツ選択": "파츠 선택",
    "強化パ-ツ装備": "파츠 장착",
    "登場作品": "등장작품",
    "このデ-タは使用できません。": "사용할 수 없는 데이터",
}

# The renderer allocates fixed Japanese cell spans for these fields.  Keep
# the full translations in the overlay, but use the reviewed compact form
# whenever the same span is used as a menu/status label.
UI_DISPLAY_COMPACTION.update({
    # Stateful SECOND UI master.  These forms were selected against the
    # retail renderer's actual (advance, wide-phase) state, not byte length or
    # Unicode character count.  The encoder below supplies only the remaining
    # invisible low/high padding required by the following VM token.
    "ユニット能力　パイロット能力　武器性能": "유닛능력 파일럿능력 무기성능표",
    "精神ポイント": "정신 점수",
    "精神コマンド": "정신 명령",
    "クリティカル補正": "크리티컬보정",
    "次のレベルまであと": "다음레벨까지",
    "パイロット:": "파일럿명",
    "ケ-ブル": "연결",
    "EN攻消費": "공격력EN",
    "EN防消費": "방어력EN",
    "イデオンゲ-ジ": "이데온량",
    "いいえ": "아뇨",
    "フェイズを終了してもよろしいですか?": "페이즈 종료할까요?",
    "フェイズを終了します": "페이즈 종료함",
    "デ-タセ-ブ": "저장",
    "のりかえ": "환승",
    "強化パ-ツ": "강화파츠",
    "次のマップへ": "다음 맵",
    "」までクリア": "」까지",
    "登録キャラクタ-NO・": "등록캐릭터NO.",
    "L.Rボタンによりキャラクタ-変更": "L/R로 캐릭터 변경",
    "誕生日": "출생일",
    "ひらがな": "히라",
    "カタカナ": "가타",
    "名前を入力してください。": "이름을 입력하세요.",
    "ボ-ナス経験値": "보너스 경험",
    "レベルアップ　レベル": "레벨업　레벨",
    "パイロット": "조종",
    "段階まで)": "단계)",
    "ユニット特別ボ-ナス": "유닛 특전",
    "全てのパラメ-タを最大まで改造したので,特別ボ-ナスがつきます。": "모든 능력치를 최대로 개조해 특전을 받습니다.",
    "以下の中から1つだけ選択して下さい。": "하나만 선택하십시오.",
    "を": " ",
    "カ-トリッジRAM": "카트리지RAM",
    "話までクリア": "화까지",
    "新規デ-タ": "새데이터",
    "この組み合わせでいいですか?": "이 조합으로 할까요?",
    "この武器のパラメ-タを最大まで改造したので,特別ボ-ナスが": "무기를 최대로 개조해 특전을 받습니다.",
    "つきます。資金(": "추가 자금(",
    "されます。投入しますか?": "추가 진행?",
    "このデ-タは使用できません。": "사용 불가 데이터",
    "マップをみる": "지도보기",
    "デ-タ": "자료",
    "ユニット": "유닛",
    "フェイズ終了": "페이즈종료",
    "部隊表": "부대표",
    "反撃命令": "반격명령",
    "作戦目的": "작전목적",
    "精神検索": "정신검색",
    "精神検索一覧": "정신검색",
    "出撃ユニット選択　あと": "출격유닛선택　남은",
    "行動終了していないユニットが　　体あります": "미행동 유닛　　기",
    "積極的に!": "적극!",
    "効率よく!": "효율!",
    "反撃するな!": "반격금지!",
    "パイロット:": "파일럿:",
    "ロボット:": "로봇:",
    "武器:": "무기:",
    ":アニメ:": ":애니:",
    "ダメ-ジ": "피해",
    "ツバゼリ": "칼날",
    "盾防": "방어",
    "EN攻消費": "공격EN",
    "EN防消費": "방어EN",
    "イデオンゲ-ジ": "이데온게이지",
    "サ-ベル回避": "사벨회피",
    "システム設定": "시스템설정",
    "スクエア": "정방 ",
    "サウンド": "사운드",
    "戦闘BGM設定": "전투BGM설정",
    "特殊操作": "특수조작",
    "+セレクト+スタ-トでリセット": "+선+시작 리셋     ",
    "セレクトを押し続けていれば": "셀렉트를계속누르면",
    "クイックコンティニュー": "빠른계속     ",
    "クイックコンティニュ-": "빠른이어하기",
    "ボタン設定": "버튼설정",
    "決定": "결정",
    "キャンセル": "취소",
    "スピ-ドアップ": "속도올리기",
    "全体マップ": "전체맵",
    "自軍ユニット送り": "다음아군유닛",
    "自軍ユニット戻し": "이전아군유닛",
    "敵軍ユニット送り": "다음적군유닛",
    "敵軍ユニット戻し": "이전적군유닛",
    "初期設定に戻す": "기본설정복원",
    "勝利条件": "승리조건",
    "敗北条件": "패배조건",
    "これでいい": "확정",
    "どの能力を改造しますか?": "개조할 능력?",
    "空.陸.海.宇のいずれかの地形適応をAにできる。": "공·육·해·우 중 하나를 A로",
    "制限をうける能力　　　()内は本来の能力": "제한능력(원래)",
    "名前と愛称を入力してください。": "이름·애칭 입력",
    "装備中のパ-ツ": "장착파츠",
    "強化パ-ツ選択": "강화파츠선택",
    "強化パ-ツ装備": "강화파츠장착",
    "スタ-ト": "시작",
    "ロ-ド": "로드",
    "コンティニュ-": "이어하기",
    "オプション": "옵션",
    "セ-ブ": "저장",
    "デ-タセ-ブ": "데이터저장",
    "新規データ": "새데이터",
    "のりかえ": "갈아타기",
    "登録キャラクターの中から選択する": "등록캐릭터 선택",
    "L.Rボタンによりキャラクター変更": "L/R로 변경",
    "ボーナス経験値": "보너스EXP",
    "ユニット修理": "유닛수리",
    "ユニット特別ボーナス": "유닛보너스",
    "強化パーツ": "강화파츠",
    "オプションモード": "옵션모드",
    "サウンドセレクト": "사운드선택",
    "キャラクター事典": "캐릭터사전",
    "ロボット大図鑑": "로봇도감",
    "デモセレクト": "데모선택",
    "カラオケモ-ド": "가라오케",
    "カラオケモード": "가라오케",
    "ステレオ": "입체",
    "モノラル": "모노",
    "声優": "성우",
    "全長": "전장",
    "重量": "중량",
})

# Keep the phase-reviewed master forms last: several older fixed-cell aliases
# above intentionally share the same Japanese key, but were authored before
# the alternating wide-glyph renderer was understood.
UI_DISPLAY_COMPACTION.update({
    "パイロット:": "파일럿명",
    "EN攻消費": "공격력EN",
    "EN防消費": "방어력EN",
    "イデオンゲ-ジ": "이데온량",
    "デ-タセ-ブ": "저장",
    "のりかえ": "환승",
    "の全滅": " 전멸",
    "味方の全滅": "아군 전멸",
})

# Music/demo titles share one fixed-width list renderer.  These reviewed
# aliases are the only entries whose full Korean title exceeds the retail
# record's stateful layout signature; all other entries keep their full text
# and are padded invisibly by _rebuild_music_demo_title().
MUSIC_DEMO_DISPLAY_COMPACTION_BY_INDEX = {
    3: "극장판 MZ", 4: "G마징가", 5: "겟타로보", 6: "겟타로보G",
    7: "초전자 로보컴배틀러V", 9: "성전단바인", 10: "성전단바인 OVA",
    11: "중전엘가임", 13: "기동전사 Z건담", 14: "기동전사 건담ZZ",
    15: "기동전사 건담0080", 16: "기동전사 건담0083", 17: "샤아역습",
    18: "기동전사 건담F91", 21: "신세기 에바", 22: "톱을노려!",
    23: "전설의이데온", 25: "신기동전기 건담W", 26: "창작",
    28: "UFO GRENDIZER", 30: "차기작예고편", 31: "컴배틀러 V합체",
    32: "마징가발진", 33: "제트 스크랜더발진", 35: "G마징가 발진",
    38: "진겟타로보변형", 39: "그룬가스트〜G랜더", 40: "그룬〜윙가스트",
    41: "카이저발진", 42: "톱을노려! 건버스터", 43: "에바 초호기 발진",
    44: "최후사자 카오루", 45: "겟타 1변형", 46: "겟타 2변형",
    47: "겟타 3변형", 49: "둘의 만남", 50: "석파러브 천경권",
    51: "동방불패의최후", 52: "레인M트레이스", 55: "궁극석파천경권",
    56: "H레프러칸", 57: "H라이네크", 58: "H즈와우스", 59: "H갈라바",
    60: "겟타D 변형", 61: "겟타L 변형", 62: "겟타포세이돈변형",
    67: "단바인난다", 68: "사일런트V", 69: "겟타로보!",
    71: "나는 G마징가", 73: "Z 테마", 74: "마징가Z비행",
    76: "컴히어!다이탄3", 77: "컴배틀러V 테마",
    78: "톱을노려!〜FLY HIGH〜", 79: "이데 부활", 80: "그대와우주",
    81: "잔혹천사 테제", 83: "열풍!질풍!CYBUSTER", 84: "날아라G다이저",
    87: "「시간너머」", 88: "「계획짜?」", 92: "「모빌슈트전〜적기습격」",
    93: "「사일런트V」", 97: "「단바인난다」", 100: "「F91 건담출격」",
    102: "「불타는투지」", 103: "「겟타로보!」", 105: "「Z 테마」",
    106: "「마징가Z비행」", 107: "「나는 G마징가」",
    108: "「컴히어!다이탄3」", 109: "「컴배틀러V 테마」",
    113: "「이데 부활」", 114: "「현비상」", 115: "「그대와우주」",
    116: "「톱을노려!〜FLY HIGH〜」", 118: "「잔혹천사 테제」",
    121: "「열풍!질풍!CYBUSTER」", 123: "「불꽃중화교사」",
    124: "「물・늪의나라」", 126: "「미오 정통존가라」", 127: "「다크감옥」",
    129: "「출격준비?」", 130: "「힘・기」", 132: "「아득저편」",
    133: "「어둠사자」", 134: "「100광년용기」", 135: "「학살 머신」",
    137: "「마르스B」", 140: "「증원부대출현」", 141: "「부제」",
    143: "「사망?」", 145: "「자,작전짤까?」",
    146: "「교향곡9번 라단조 4악장에서」", 147: "「끝이에요」",
    148: "「날아라G다이저」", 156: "「랑그란바람」", 157: "「정령가호」",
    165: "「잠깐쉼」", 168: "「마사키부제」", 169: "「류네부제」",
    170: "「슈우부제」",
}

# A second exact-layout pass found these titles at the boundary where the
# Korean high-glyph phase itself adds one more unit.  They fit by character
# count but not by the retail stateful cursor signature.
MUSIC_DEMO_DISPLAY_COMPACTION_BY_INDEX.update({
    0: "캐릭터사전", 2: "마징Z", 8: "무적다이탄3", 20: "초수신단쿠가",
    27: "용자라이딘", 34: "비너스A발진", 37: "단쿠 합체", 48: "다이탄",
    65: "엘가임〜TIMEFOR L-GAIM〜", 70: "고쇼군발진하라", 72: "마징Z",
    85: "용자라이딘", 91: "「늠름 샤아」", 95: "「엘가임〜TIMEFOR L-GAIM〜」",
    98: "「뉴담」", 104: "「마징Z」", 110: "「고쇼군발진하라」",
    144: "「레퀴」", 149: "「용자라이딘」", 159: "「소곤」",
    162: "「잠깐휴식」",
})

# Names are relocated into the guarded pointer pool, but battle/status
# renderers still reserve the original Japanese cell count.  These aliases
# are selected only for fixed-width name fields; dialogue is untouched.
UI_NAME_COMPACTION_BY_TEXT = {
    "코우지": "코지", "유미 교수": "유미", "모리모리 박사": "모리모리",
    "세와시 박사": "세와시", "테츠야": "테츠", "사오토메 박사": "사오토메",
    "다이사쿠": "다이", "코스케": "코스", "요츠야 박사": "요츠야",
    "아키라": "아", "레이": "레", "사루마루": "사루", "DC 병사": "DC병",
    "개량 인공지능": "인공지능", "연구원": "연구", "강화 바이오로이드": "강화바이오",
    "바이오로이드 병사": "바이오로이드", "DC 저격병": "DC저격", "미네르바 X": "미네르바X",
    "젊은 남자": "젊은남", "젊은 여자": "젊은녀", "연방군 병사": "연방병",
    "DC 강화병": "DC강화",
    "카부토 코우지": "코우지", "유미 사야카": "사야카", "츠루기 테츠야": "테츠야",
    "호노오 쥰": "쥰", "나가레 료마": "료마", "진 하야토": "하야토",
    "쿠루마 벤케이": "벤케이", "사오토메 미치루": "미치루", "아오이 효마": "효마",
    "나니와 쥬조": "쥬조", "니시카와 다이사쿠": "다이사쿠", "난바라 치즈루": "치즈루",
    "키타 코스케": "코스케", "하란 반죠": "반죠", "토다 톳타": "톳타",
    "토모에 무사시": "무사시", "히비키 아키라": "아키라", "아스카 레이": "레이",
    "사쿠라노 마리": "마리", "진구지 리키": "리키", "사루마루 타로": "타로",
    "마키바 히카루": "히카루",
    "건담 Mk-II": "건담II", "GP-02A 사이살리스": "GP02사이살리스",
    "자쿠 III 개량형": "자쿠3", "자쿠 III": "자쿠3", "아프로다이 A": "아프로A",
    "사이코 건담 Mk-II(MS)": "사이코건담II", "사이코 건담 Mk-II(MA)": "사이코건담II",
    "자쿠 II": "자쿠2", "구시오스 베타 III": "구시오스3", "자쿠 개량형": "자쿠개",
    "돔 II": "돔2", "구형 자쿠": "구자쿠", "큐베레이 Mk-II": "큐베레이2",
    "갈루스 J": "갈루스J", "젠 II": "젠2", "다브라스 M2": "다브라스2",
    "가라다 K7": "가라다7", "토로스 D7": "토로스7", "아브도라 U6": "아브도라6",
    "마그마수 가르무스": "마그마수", "개량형 무사이": "무사이개",
    "메카 뇌수귀": "메카뇌귀", "메카 호접귀": "메카호귀", "고정 포대": "고정포대",
    "메카 철갑귀": "메카철귀", "샤아 전용 자쿠 II": "샤아자쿠2",
    "가자 C(MS)": "가자C(MS)", "가자 C(MA)": "가자C(MA)",
    "가자 D(MS)": "가자D(MS)", "가자 D(MA)": "가자D(MA)",
}


def _visible_glyph_capacity(raw_hex: str | None) -> int | None:
    """Return the renderer-cell capacity of a guarded fixed-width record."""

    if not raw_hex:
        return None
    raw = bytes.fromhex(str(raw_hex))
    _end, tokens = _parse_record(raw, 0, len(raw), RENDERER_GRAMMAR)
    return sum(1 for token in tokens if token.kind == "glyph")


FIXED_POINTER_TEXT_ASSETS = {
    "terrain_combinations",
    "terrain_names",
    "spirit_commands",
    "enhancement_parts",
    "weapon_names",
    "pilot_skills",
    "unit_abilities",
    "scenario_titles",
    "pilot_short_names",
    "pilot_full_names",
    "unit_names",
}

# The pilot/unit *name* lists are drawn in a fixed display column sized to the
# longest retail record, so a Korean name is allowed to exceed its own retail
# katakana advance up to that shared column width.  This keeps proper full
# names (아무로, 브라이트, …) instead of syllable-dropping aliases.  Assets that
# position a following inline field by the record's own cursor are excluded.
RELAXED_COLUMN_NAME_ASSETS = {
    "pilot_short_names",
    "pilot_full_names",
    "unit_names",
}

# These retail numeric slots intentionally identify different characters in
# the short battle-label and full pilot-data tables.  Falling back by index
# would put the wrong person's name on screen.
PILOT_FULL_SHORT_FALLBACK_EXCLUDED_INDICES = {10, 12, 54, 63, 101}


def _without_display_spaces(text: str) -> str:
    return text.replace(" ", "").replace("\u3000", "")


_REVIEWED_SOURCE_CHARACTERS: dict[int, str | None] | None = None


def _reviewed_source_characters() -> dict[int, str | None]:
    global _REVIEWED_SOURCE_CHARACTERS
    if _REVIEWED_SOURCE_CHARACTERS is None:
        path = Path(__file__).resolve().parents[1] / "research" / "srwcb_embedded_font_mapping_reviewed.json"
        document = json.loads(path.read_text(encoding="utf-8"))
        _REVIEWED_SOURCE_CHARACTERS = {
            int(row["glyph_index"]): row.get("character")
            for row in document["rows"]
        }
    return _REVIEWED_SOURCE_CHARACTERS


def _infer_ui_text_replacements(
    raw: bytes,
    existing: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Find uncovered Japanese glyph runs and return guarded replacements."""

    _end, tokens = _parse_record(raw, 0, len(raw), SECOND_UI_VM_GRAMMAR)
    covered = [
        (
            _int(item["relative_start"], "relative_start"),
            _int(
                item.get("relative_end", item.get("relative_end_exclusive")),
                "relative_end",
            ),
        )
        for item in existing
    ]
    chars_by_index = _reviewed_source_characters()
    inferred: list[dict[str, Any]] = []
    run: list[RendererToken] = []

    def flush() -> None:
        if not run:
            return
        start = run[0].start
        end = run[-1].end
        chars: list[str] = []
        for token in run:
            raw_token = token.raw
            index = (
                raw_token[0]
                if len(raw_token) == 1
                else ((raw_token[0] - 0xEB) << 8) | raw_token[1]
            )
            character = chars_by_index.get(index)
            chars.append(" " if index == 0 else (character or ""))
        source_text = "".join(chars)
        korean = COMPLETE_UI_TEXT_TRANSLATIONS.get(source_text)
        if (
            korean is not None
            and source_text
            and not any(a < end and b > start for a, b in covered)
        ):
            inferred.append(
                {
                    "relative_start": start,
                    "relative_end": end,
                    "korean_text": korean,
                    "japanese_text": source_text,
                }
            )
        run.clear()

    for token in tokens:
        if token.kind == "glyph":
            run.append(token)
        else:
            flush()
    flush()
    return inferred


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def u32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def s32(data: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def align_up(value: int, alignment: int) -> int:
    if alignment <= 0 or alignment & (alignment - 1):
        raise ValueError("alignment must be a positive power of two")
    return (value + alignment - 1) & -alignment


def encode_glyph_index(index: int) -> bytes:
    if not 0 <= index < 0xB00:
        raise ValueError(f"glyph index outside font: 0x{index:X}")
    if index < 0xEB:
        return bytes((index,))
    return bytes((0xEB + (index >> 8), index & 0xFF))


def normalise_ui_text(text: str) -> str:
    return "".join(CHAR_NORMALISATION.get(char, char) for char in text)


def encode_ui_text(text: str, glyph_map: dict[str, int], *, terminate: bool) -> bytes:
    """Encode UI text without dialogue wrapping; newlines become renderer F6."""

    text = normalise_ui_text(text)
    # U+30FB is punctuation retained in the safe low font; it is not Japanese
    # language content for this audit.
    bad = sorted(
        {
            char
            for char in text
            if JAPANESE_OR_HAN_RE.fullmatch(char) and char != "・"
        }
    )
    if bad:
        rendered = " ".join(f"{char}(U+{ord(char):04X})" for char in bad)
        raise ValueError(f"Japanese/Han remains in Korean UI text: {rendered}")
    output = bytearray()
    for char in text:
        if char == "\r":
            continue
        if char == "\n":
            output.append(0xF6)
            continue
        try:
            index = glyph_map[char]
        except KeyError as exc:
            raise ValueError(
                f"no allocated font glyph for UI character {char!r} U+{ord(char):04X}"
            ) from exc
        output.extend(encode_glyph_index(index))
    if terminate:
        output.append(0xFF)
    return bytes(output)


@dataclass(frozen=True)
class RendererToken:
    start: int
    end: int
    kind: str
    raw: bytes


def parse_renderer_record(data: bytes | bytearray, start: int, limit: int | None = None) -> tuple[int, list[RendererToken]]:
    if limit is None:
        limit = len(data)
    cursor = start
    tokens: list[RendererToken] = []
    while cursor < limit:
        token_start = cursor
        opcode = data[cursor]
        if opcode < 0xEB:
            cursor += 1
            tokens.append(RendererToken(token_start, cursor, "glyph", bytes(data[token_start:cursor])))
            continue
        if opcode < 0xF6:
            if cursor + 2 > limit:
                raise ValueError(f"truncated two-byte glyph at 0x{cursor:X}")
            cursor += 2
            tokens.append(RendererToken(token_start, cursor, "glyph", bytes(data[token_start:cursor])))
            continue
        if opcode == 0xFF:
            cursor += 1
            tokens.append(RendererToken(token_start, cursor, "terminator", b"\xFF"))
            return cursor, tokens
        arg_bytes = CONTROL_ARG_LENGTHS.get(opcode)
        if arg_bytes is None:
            raise ValueError(f"unknown renderer opcode 0x{opcode:02X} at 0x{cursor:X}")
        cursor += 1 + arg_bytes
        if cursor > limit:
            raise ValueError(f"truncated renderer control at 0x{token_start:X}")
        tokens.append(RendererToken(token_start, cursor, "control", bytes(data[token_start:cursor])))
    raise ValueError(f"unterminated renderer record at 0x{start:X}")


def parse_second_ui_vm_record(
    data: bytes | bytearray,
    start: int,
    limit: int | None = None,
) -> tuple[int, list[RendererToken]]:
    """Parse one stateful record from SECOND's pointer-backed UI master.

    The initial mode is deliberately unknown.  Every retail master record
    executes F7 before its first mode-sensitive EB..F5 byte, so assuming a
    stale runtime mode is unnecessary.  A malformed/new record that violates
    that invariant fails closed instead of silently choosing a token width.
    """

    if limit is None:
        limit = len(data)
    cursor = start
    mode: str | None = None
    tokens: list[RendererToken] = []
    while cursor < limit:
        token_start = cursor
        opcode = data[cursor]

        if opcode < 0xEB:
            cursor += 1
            kind = "glyph" if mode == "extended" else "compact_data"
            tokens.append(
                RendererToken(token_start, cursor, kind, bytes(data[token_start:cursor]))
            )
            continue

        if mode == "extended" and opcode < 0xF6:
            if cursor + 2 > limit:
                raise ValueError(f"truncated SECOND UI two-byte glyph at 0x{cursor:X}")
            cursor += 2
            tokens.append(
                RendererToken(token_start, cursor, "glyph", bytes(data[token_start:cursor]))
            )
            continue

        if mode == "compact" and opcode < 0xF0:
            cursor += 1
            tokens.append(
                RendererToken(
                    token_start,
                    cursor,
                    "compact_data",
                    bytes(data[token_start:cursor]),
                )
            )
            continue

        if opcode == 0xFF:
            cursor += 1
            tokens.append(RendererToken(token_start, cursor, "terminator", b"\xFF"))
            return cursor, tokens

        arg_bytes = SECOND_UI_VM_COMMON_ARG_LENGTHS.get(opcode)
        if arg_bytes is None and mode == "compact":
            arg_bytes = SECOND_UI_VM_COMPACT_ARG_LENGTHS.get(opcode)
        if arg_bytes is None:
            if mode is None and 0xEB <= opcode <= 0xF5:
                raise ValueError(
                    f"SECOND UI mode-sensitive byte 0x{opcode:02X} before F7 "
                    f"at 0x{cursor:X}"
                )
            raise ValueError(
                f"unknown SECOND UI opcode 0x{opcode:02X} at 0x{cursor:X} "
                f"in {mode or 'unknown'} mode"
            )

        cursor += 1 + arg_bytes
        if cursor > limit:
            raise ValueError(f"truncated SECOND UI control at 0x{token_start:X}")
        raw = bytes(data[token_start:cursor])
        tokens.append(RendererToken(token_start, cursor, "control", raw))
        if opcode == 0xF7:
            argument_word = (raw[1] << 8) | raw[2]
            mode = "extended" if argument_word & 0x40 else "compact"

    raise ValueError(f"unterminated SECOND UI record at 0x{start:X}")


def _parse_record(
    data: bytes | bytearray,
    start: int,
    limit: int | None,
    grammar: str,
) -> tuple[int, list[RendererToken]]:
    if grammar == RENDERER_GRAMMAR:
        return parse_renderer_record(data, start, limit)
    if grammar == SECOND_UI_VM_GRAMMAR:
        return parse_second_ui_vm_record(data, start, limit)
    raise ValueError(f"unknown UI record grammar {grammar!r}")


def record_bytes(
    data: bytes | bytearray,
    start: int,
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> bytes:
    end, _tokens = _parse_record(data, start, None, grammar)
    return bytes(data[start:end])


def control_signature(
    raw: bytes,
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> tuple[str, ...]:
    end, tokens = _parse_record(raw, 0, len(raw), grammar)
    if end != len(raw):
        raise ValueError("bytes follow renderer record terminator")
    return tuple(token.raw.hex(" ").upper() for token in tokens if token.kind == "control")


def _int(value: Any, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 0)
    raise ValueError(f"{field} must be an integer, got {value!r}")


def _verify_record_guard(
    executable: bytes | bytearray,
    row: dict[str, Any],
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> bytes:
    start = _int(row.get("source_offset", row.get("target")), "source_offset")
    expected_hex = row.get("raw_hex") or row.get("source_hex")
    if expected_hex:
        expected = bytes.fromhex(str(expected_hex))
        raw = bytes(executable[start:start + len(expected)])
        if raw != expected:
            raise ValueError(f"source bytes changed for record at 0x{start:X}")
    elif row.get("end_offset_exclusive") is not None:
        end = _int(row["end_offset_exclusive"], "end_offset_exclusive")
        if not start < end <= len(executable):
            raise ValueError(f"invalid guarded record span 0x{start:X}..0x{end:X}")
        raw = bytes(executable[start:end])
    elif row.get("byte_length") is not None:
        length = _int(row["byte_length"], "byte_length")
        if length <= 0 or start + length > len(executable):
            raise ValueError(f"invalid guarded record length at 0x{start:X}")
        raw = bytes(executable[start:start + length])
    else:
        raw = record_bytes(executable, start, grammar=grammar)
    expected_hash = row.get("raw_sha256") or row.get("source_sha256")
    if expected_hash and sha256(raw) != str(expected_hash).lower():
        raise ValueError(f"source SHA-256 changed for record at 0x{start:X}")
    return raw


def _span_boundaries(raw: bytes, *, grammar: str = RENDERER_GRAMMAR) -> set[int]:
    _end, tokens = _parse_record(raw, 0, len(raw), grammar)
    return {0, *(token.start for token in tokens), *(token.end for token in tokens)}


def _renderer_glyph_index(raw: bytes) -> int:
    if len(raw) == 1:
        return raw[0]
    if len(raw) == 2 and 0xEB <= raw[0] < 0xF6:
        return ((raw[0] - 0xEB) << 8) | raw[1]
    raise ValueError(f"not a renderer glyph token: {raw.hex(' ').upper()}")


def _renderer_span_advance(
    tokens: Iterable[RendererToken],
    *,
    initial_wide_phase: int = 0,
) -> tuple[int, int]:
    """Model the stateful cursor advance in 0x8006F25C..0x8006F2A8.

    Glyph indices below 0x101 advance one unit.  A high glyph advances one
    unit in phase zero or two units in phase one, then toggles the phase.  The
    settings pages enable the F6 mode that resets this phase at every line,
    so each reviewed fixed span starts in phase zero.
    """

    phase = initial_wide_phase
    advance = 0
    for token in tokens:
        if token.kind != "glyph":
            continue
        if _renderer_glyph_index(token.raw) < 0x101:
            advance += 1
        else:
            advance += 1 + phase
            phase ^= 1
    return advance, phase


def _renderer_layout_signature(
    tokens: Iterable[RendererToken],
) -> tuple[int, int]:
    """Return the phase-zero advance and final wide phase of glyph tokens.

    Equal signatures are equivalent for either incoming phase.  This is the
    invariant required immediately before static glyphs, FC anchors and F8
    dynamic fields in SECOND's stateful UI VM.
    """

    return _renderer_span_advance(tokens, initial_wide_phase=0)


def _next_token_requires_matching_phase(token: RendererToken | None) -> bool:
    if token is None or token.kind == "terminator":
        return False
    if token.kind == "glyph":
        return True
    if token.kind != "control":
        return False
    # F6 resets the wide phase.  FE/FD end the current draw path, so only the
    # visible width matters there as well.  FC and F8 consume the current
    # cursor/phase; all other controls are kept conservative.
    return token.raw[0] not in {0xF6, 0xFD, 0xFE}


def _encode_fixed_span_text(
    text: str,
    glyph_map: dict[str, int],
    capacity: int,
    *,
    preserve_width: bool,
    byte_capacity: int | None = None,
    pixel_capacity: int | None = None,
    renderer_layout: tuple[int, int] | None = None,
    require_matching_phase: bool = True,
) -> bytes:
    """Encode a label and retain its original renderer-cell/byte span.

    SECOND's menu VM positions later fields relative to the current cursor and
    several settings records contain byte-addressed subfields.  A shorter
    Korean label therefore needs renderer-space padding, but the replacement
    must also stay within the original byte span.  Padding by cell count alone
    is unsafe because Hangul glyphs are two-byte indices while many Japanese
    labels use one-byte indices.  Dialogue and variable tables deliberately
    opt out of this behavior.
    """

    encoded = encode_ui_text(text, glyph_map, terminate=False)
    if not preserve_width or "\n" in text:
        return encoded
    visible_cells = len(normalise_ui_text(text).replace("\r", ""))
    if renderer_layout is None and visible_cells > capacity:
        raise ValueError(
            f"fixed UI span needs {visible_cells} cells but has capacity {capacity}"
        )
    if renderer_layout is not None:
        _end, encoded_tokens = _parse_record(
            encoded + b"\xFF", 0, len(encoded) + 1, RENDERER_GRAMMAR
        )
        target_advance, target_phase = renderer_layout
        encoded_advance, encoded_phase = _renderer_layout_signature(encoded_tokens)
        if require_matching_phase and encoded_phase != target_phase:
            # Append the retail invisible high glyph to flip phase while
            # retaining a blank visual cell.  Its advance depends on the
            # phase at this exact point, just like every other high glyph.
            encoded += RENDERER_HIGH_BLANK
            encoded_advance += 1 + encoded_phase
            encoded_phase ^= 1
        if encoded_advance > target_advance:
            raise ValueError(
                f"fixed UI span advances {encoded_advance} units but has "
                f"capacity {target_advance}"
            )
        if require_matching_phase and encoded_phase != target_phase:
            raise ValueError("fixed UI span cannot preserve the renderer phase")
        return encoded + b"\x00" * (target_advance - encoded_advance)
    if pixel_capacity is not None:
        _end, encoded_tokens = _parse_record(
            encoded + b"\xFF", 0, len(encoded) + 1, RENDERER_GRAMMAR
        )
        encoded_width = 8 * _renderer_span_advance(encoded_tokens)[0]
        if encoded_width > pixel_capacity:
            raise ValueError(
                f"fixed UI span needs {encoded_width} pixels but has capacity {pixel_capacity}"
            )
        remaining = pixel_capacity - encoded_width
        if remaining % 8:
            raise ValueError(
                f"fixed UI pixel capacity is not an 8-pixel multiple: {pixel_capacity}"
            )
        return encoded + b"\x00" * (remaining // 8)
    if byte_capacity is not None:
        if len(encoded) > byte_capacity:
            raise ValueError(
                f"fixed UI span needs {len(encoded)} bytes, "
                f"but target capacity is {byte_capacity}"
            )
        # These selector records are indexed by raw byte stride, not by source
        # glyph count.  Two Hangul glyphs already fill a four-byte field;
        # source-token padding would make it six bytes and shift every later
        # value in the table.
        return encoded + b"\x00" * (byte_capacity - len(encoded))
    return encoded + b"\x00" * (capacity - visible_cells)


def apply_span_replacements(
    raw: bytes,
    replacements: list[dict[str, Any]],
    glyph_map: dict[str, int],
    *,
    grammar: str = RENDERER_GRAMMAR,
    preserve_display_width: bool = False,
    preserve_display_bytes: bool = False,
    preserve_display_pixels: bool = False,
    preserve_renderer_layout: bool = False,
) -> bytes:
    if not replacements:
        return raw
    parsed_end, record_tokens = _parse_record(raw, 0, len(raw), grammar)
    if parsed_end != len(raw):
        raise ValueError("bytes follow UI record terminator")
    boundaries = {0, *(token.start for token in record_tokens), *(token.end for token in record_tokens)}
    source_controls = control_signature(raw, grammar=grammar)
    ordered = sorted(replacements, key=lambda item: _int(item["relative_start"], "relative_start"))
    output = bytearray()
    cursor = 0
    for replacement in ordered:
        start = _int(replacement["relative_start"], "relative_start")
        end = _int(
            replacement.get("relative_end", replacement.get("relative_end_exclusive")),
            "relative_end",
        )
        if start < cursor or end < start or end > len(raw) - 1:
            raise ValueError(f"invalid or overlapping UI replacement span {start}..{end}")
        if start not in boundaries or end not in boundaries:
            raise ValueError(f"replacement span {start}..{end} splits a renderer token")
        source_hex = replacement.get("source_hex")
        source = raw[start:end] if source_hex is None else bytes.fromhex(str(source_hex))
        if raw[start:end] != source:
            raise ValueError(f"replacement source bytes differ at span {start}..{end}")
        expected_source_hash = replacement.get("source_sha256")
        if expected_source_hash and sha256(source) != str(expected_source_hash).lower():
            raise ValueError(f"replacement source SHA-256 differs at span {start}..{end}")
        span_tokens = [
            token
            for token in record_tokens
            if token.start < end and token.end > start
        ]
        if not span_tokens or any(token.kind != "glyph" for token in span_tokens):
            raise ValueError("replacement span contains a UI control or compact-mode data")
        next_token = next(
            (token for token in record_tokens if token.start >= end),
            None,
        )
        output.extend(raw[cursor:start])
        display_text = str(replacement.get("display_text")) if isinstance(
            replacement.get("display_text"), str
        ) else UI_DISPLAY_COMPACTION.get(
            str(replacement.get("japanese_text", "")),
            str(replacement["korean_text"]),
        )
        try:
            encoded_span = _encode_fixed_span_text(
                display_text,
                glyph_map,
                len(span_tokens),
                preserve_width=preserve_display_width,
                byte_capacity=(
                    _int(replacement["output_byte_capacity"], "output_byte_capacity")
                    if preserve_display_bytes and replacement.get("output_byte_capacity") is not None
                    else (end - start) if preserve_display_bytes else None
                ),
                pixel_capacity=(
                    8 * _renderer_span_advance(span_tokens)[0]
                    if preserve_display_pixels else None
                ),
                renderer_layout=(
                    _renderer_layout_signature(span_tokens)
                    if preserve_renderer_layout else None
                ),
                require_matching_phase=_next_token_requires_matching_phase(next_token),
            )
        except ValueError as exc:
            raise ValueError(
                f"UI span {start}..{end} {replacement.get('japanese_text')!r} "
                f"-> {display_text!r}: {exc}"
            ) from exc
        output.extend(encoded_span)
        cursor = end
    output.extend(raw[cursor:])
    rebuilt = bytes(output)
    if control_signature(rebuilt, grammar=grammar) != source_controls:
        raise ValueError("UI replacement changed renderer controls")
    return rebuilt


def apply_guarded_control_patches(
    raw: bytes,
    patches: list[dict[str, Any]],
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> bytes:
    """Apply reviewed equal-size VM argument edits with exact source guards."""

    if not patches:
        return raw
    _end, before_tokens = _parse_record(raw, 0, len(raw), grammar)
    before_shape = tuple(
        (
            token.kind,
            token.start,
            token.end,
            token.raw[0] if token.kind == "control" else None,
        )
        for token in before_tokens
    )
    output = bytearray(raw)
    for patch in patches:
        source = bytes.fromhex(str(patch["source_hex"]))
        replacement = bytes.fromhex(str(patch["replacement_hex"]))
        if not source or len(source) != len(replacement) or source == replacement:
            raise ValueError("guarded control patch must be unequal and equal-sized")
        hits: list[int] = []
        cursor = 0
        while True:
            offset = bytes(output).find(source, cursor)
            if offset < 0:
                break
            hits.append(offset)
            cursor = offset + 1
        expected_count = _int(patch.get("expected_count", 1), "expected_count")
        if len(hits) != expected_count:
            raise ValueError(
                f"guarded control patch expected {expected_count} hit(s), "
                f"found {len(hits)}"
            )
        for offset in hits:
            output[offset:offset + len(source)] = replacement

    rebuilt = bytes(output)
    parsed_end, after_tokens = _parse_record(rebuilt, 0, len(rebuilt), grammar)
    if parsed_end != len(rebuilt):
        raise ValueError("guarded control patch changed record boundary")
    after_shape = tuple(
        (
            token.kind,
            token.start,
            token.end,
            token.raw[0] if token.kind == "control" else None,
        )
        for token in after_tokens
    )
    if after_shape != before_shape:
        raise ValueError("guarded control patch changed VM token structure")
    return rebuilt


def rebuild_row_record(
    raw: bytes,
    row: dict[str, Any],
    glyph_map: dict[str, int],
    *,
    grammar: str = RENDERER_GRAMMAR,
) -> bytes:
    replacements = row.get("replacements")
    if isinstance(replacements, list):
        asset_id = str(row.get("asset_id", ""))
        preserve_layout = bool(row.get("preserve_renderer_layout")) or (
            asset_id == "second_ui_script_master"
            and not bool(row.get("preserve_display_pixels"))
        )
        preserve_width = (
            bool(row.get("preserve_display_width"))
            or asset_id in {"second_ui_script_master", "common_ui_master_labels"}
        )
        preserve_bytes = bool(row.get("preserve_display_bytes"))
        preserve_pixels = bool(row.get("preserve_display_pixels"))
        rebuilt = apply_span_replacements(
            raw,
            replacements,
            glyph_map,
            grammar=grammar,
            preserve_display_width=preserve_width,
            preserve_display_bytes=preserve_bytes,
            preserve_display_pixels=preserve_pixels,
            preserve_renderer_layout=preserve_layout,
        )
    else:
        korean = row.get("korean_text")
        if korean is None:
            rebuilt = raw
        else:
            if control_signature(raw, grammar=grammar) and not row.get(
                "allow_full_record_rebuild"
            ):
                raise ValueError("control-bearing row needs exact span replacements")
            prefix = bytes.fromhex(str(row.get("renderer_prefix_hex", "")))
            rebuilt = prefix + encode_ui_text(
                str(korean), glyph_map, terminate=True
            )
            if (
                not row.get("allow_control_change")
                and control_signature(raw, grammar=grammar)
                != control_signature(rebuilt, grammar=grammar)
            ):
                raise ValueError("full UI record rebuild changed controls")
    control_patches = row.get("control_patches")
    if isinstance(control_patches, list):
        rebuilt = apply_guarded_control_patches(
            rebuilt, control_patches, grammar=grammar
        )
    return rebuilt


def rebuild_preview_record(
    raw: bytes, overlay: dict[str, Any], glyph_map: dict[str, int]
) -> bytes:
    """Rebuild one preview/condition record from its overlay.

    Preview dialogue overlays carry dialogue-codec ``translation_parts`` so the
    Korean text is re-wrapped to the 20-cell window while every control token is
    preserved verbatim; simpler condition labels fall back to the shared
    span/text rebuild path.
    """

    parts = overlay.get("translation_parts")
    if parts is not None:
        rebuilt, _layout = assemble_translated_record(
            parts, overlay.get("ko_parts", {}), glyph_map
        )
        return rebuilt
    return rebuild_row_record(raw, overlay, glyph_map)


@dataclass
class StaticPool:
    """Best-fit allocator over guarded, possibly fragmented record spans."""

    spans: list[tuple[int, int]]
    free: list[tuple[int, int]] = field(init=False)
    allocations: list[dict[str, Any]] = field(default_factory=list)
    interned: dict[bytes, int] = field(default_factory=dict)
    payloads: dict[int, bytes] = field(default_factory=dict)

    def __post_init__(self) -> None:
        ordered = sorted(self.spans)
        merged: list[tuple[int, int]] = []
        for start, end in ordered:
            if not 0 <= start < end <= RETAIL_FILE_BYTES:
                raise ValueError(f"invalid static-pool span 0x{start:X}..0x{end:X}")
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))
        self.spans = merged
        self.free = list(merged)

    @property
    def capacity(self) -> int:
        return sum(end - start for start, end in self.spans)

    @property
    def used(self) -> int:
        return sum(len(raw) for raw in self.payloads.values())

    def add(
        self,
        raw: bytes,
        *,
        asset_id: str,
        key: str,
        intern: bool = True,
        alignment: int = 1,
    ) -> int:
        if not raw:
            raise ValueError("cannot allocate an empty static payload")
        if alignment <= 0 or alignment & (alignment - 1):
            raise ValueError("alignment must be a positive power of two")
        if intern and raw in self.interned:
            return self.interned[raw]

        candidates: list[tuple[int, int, int, int]] = []
        for ordinal, (start, end) in enumerate(self.free):
            aligned = align_up(start, alignment)
            if aligned + len(raw) <= end:
                candidates.append((end - start, aligned - start, ordinal, aligned))
        if not candidates:
            raise ValueError(
                f"static record pool has no {len(raw)}-byte span for {asset_id}:{key}"
            )
        _span_size, _padding, ordinal, offset = min(candidates)
        start, end = self.free.pop(ordinal)
        replacement: list[tuple[int, int]] = []
        if start < offset:
            replacement.append((start, offset))
        payload_end = offset + len(raw)
        if payload_end < end:
            replacement.append((payload_end, end))
        self.free.extend(replacement)
        self.free.sort()

        self.payloads[offset] = raw
        self.allocations.append(
            {
                "asset_id": asset_id,
                "key": key,
                "file_offset": offset,
                "ram_address": PSX_FILE_TO_RAM_BIAS + offset,
                "size": len(raw),
                "sha256": sha256(raw),
            }
        )
        if intern:
            self.interned[raw] = offset
        return offset

    def commit(self, executable: bytearray) -> None:
        occupied: list[tuple[int, int]] = []
        for offset, raw in sorted(self.payloads.items()):
            end = offset + len(raw)
            if occupied and offset < occupied[-1][1]:
                raise AssertionError("static-pool allocations overlap")
            occupied.append((offset, end))
            executable[offset:end] = raw
        for offset, raw in self.payloads.items():
            if executable[offset:offset + len(raw)] != raw:
                raise AssertionError("static-pool payload verification failed")


def _merge_intervals(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if start >= end:
            raise ValueError(f"invalid interval 0x{start:X}..0x{end:X}")
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _subtract_intervals(
    spans: Iterable[tuple[int, int]],
    removed: Iterable[tuple[int, int]],
) -> list[tuple[int, int]]:
    cuts = _merge_intervals(removed)
    output: list[tuple[int, int]] = []
    for start, end in _merge_intervals(spans):
        cursor = start
        for cut_start, cut_end in cuts:
            if cut_end <= cursor:
                continue
            if cut_start >= end:
                break
            if cursor < cut_start:
                output.append((cursor, min(cut_start, end)))
            cursor = max(cursor, cut_end)
            if cursor >= end:
                break
        if cursor < end:
            output.append((cursor, end))
    return output


def _guard_untracked_donor_references(
    executable: bytes | bytearray,
    donor_spans: Iterable[tuple[int, int]],
    donor_record_spans: Iterable[tuple[int, int]],
    known_pointer_fields: set[int],
) -> tuple[list[tuple[int, int]], list[tuple[int, int, bytes]], dict[str, Any]]:
    """Exclude records hit by any untracked aligned self-relative word.

    SECOND contains secondary alias/resource tables that are absent from the
    authoritative UI inventories.  Some fields point into the middle of a
    renderer record.  Reusing such a record would corrupt that secondary
    reader even though every primary UI pointer was repointed correctly.

    The scan intentionally accepts false positives: preserving a few extra
    source records is safe, while overwriting one real hidden target is not.
    Candidate fields located inside donor text are ignored because their four
    glyph bytes are not pointer storage.
    """

    merged_donors = _merge_intervals(donor_spans)
    records = list(donor_record_spans)

    def contains(spans: Iterable[tuple[int, int]], offset: int) -> bool:
        return any(start <= offset < end for start, end in spans)

    candidate_fields: list[int] = []
    candidate_targets: list[int] = []
    protected_records: list[tuple[int, int]] = []
    for field in range(0, len(executable) - 3, 4):
        if field in known_pointer_fields or contains(merged_donors, field):
            continue
        target = field + s32(executable, field)
        if not contains(merged_donors, target):
            continue
        owners = [
            (start, end)
            for start, end in records
            if start <= target < end
        ]
        if not owners:
            raise ValueError(
                f"untracked relative field 0x{field:X} targets donor byte "
                f"0x{target:X} outside every guarded record"
            )
        candidate_fields.append(field)
        candidate_targets.append(target)
        protected_records.extend(owners)

    protected = _merge_intervals(protected_records)
    safe_spans = _subtract_intervals(merged_donors, protected)
    snapshots = [
        (start, end, bytes(executable[start:end]))
        for start, end in protected
    ]
    return safe_spans, snapshots, {
        "aligned_untracked_reference_count": len(candidate_fields),
        "protected_record_span_count": len(protected),
        "protected_record_bytes": sum(end - start for start, end in protected),
        "candidate_fields": candidate_fields,
        "candidate_targets": candidate_targets,
    }


def _guard_static_font_donor(
    executable: bytes | bytearray,
    glyph_map: dict[str, int],
) -> list[tuple[int, int]]:
    forbidden = [
        (char, index)
        for char, index in glyph_map.items()
        if STATIC_FONT_DONOR_GLYPH_START <= index < STATIC_FONT_DONOR_GLYPH_END
    ]
    if forbidden:
        rendered = ", ".join(f"{char!r}=0x{index:X}" for char, index in forbidden[:8])
        raise ValueError(f"static font-donor glyphs are assigned by final map: {rendered}")
    raw = bytes(executable[STATIC_FONT_DONOR_START:STATIC_FONT_DONOR_END])
    if sha256(raw) != STATIC_FONT_DONOR_SHA256:
        raise ValueError("SECOND guarded static font-donor tail changed")
    expected_bytes = (
        STATIC_FONT_DONOR_GLYPH_END - STATIC_FONT_DONOR_GLYPH_START
    ) * 32
    if len(raw) != expected_bytes:
        raise AssertionError("static font-donor glyph/file bounds disagree")
    spans: list[tuple[int, int]] = []
    cursor = STATIC_FONT_DONOR_START
    for index in STATIC_FONT_DONOR_EXCLUDED_GLYPHS:
        glyph_start = STATIC_FONT_DONOR_START + (
            index - STATIC_FONT_DONOR_GLYPH_START
        ) * 32
        if cursor < glyph_start:
            spans.append((cursor, glyph_start))
        cursor = glyph_start + 32
    if cursor < STATIC_FONT_DONOR_END:
        spans.append((cursor, STATIC_FONT_DONOR_END))
    return spans


def _font_tail_glyph_references(
    raw: bytes,
    *,
    grammar: str,
    stream: bool = False,
) -> set[int]:
    references: set[int] = set()
    cursor = 0
    while cursor < len(raw):
        end, tokens = _parse_record(raw, cursor, len(raw), grammar)
        for token in tokens:
            if token.kind != "glyph" or len(token.raw) != 2:
                continue
            index = ((token.raw[0] - 0xEB) << 8) | token.raw[1]
            if STATIC_FONT_DONOR_GLYPH_START <= index < STATIC_FONT_DONOR_GLYPH_END:
                references.add(index)
        cursor = end
        if not stream:
            if cursor != len(raw):
                raise ValueError("bytes follow final-UI renderer record")
            break
    return references


def _audit_allowed_changes(
    source: bytes,
    patched: bytes | bytearray,
    allowed_spans: Iterable[tuple[int, int]],
) -> dict[str, Any]:
    if len(source) != len(patched):
        raise AssertionError("UI repack changed executable length before change audit")
    merged = _merge_intervals(allowed_spans)
    span_index = 0
    changed = 0
    unexpected: list[int] = []
    for offset, (before, after) in enumerate(zip(source, patched)):
        if before == after:
            continue
        changed += 1
        while span_index < len(merged) and merged[span_index][1] <= offset:
            span_index += 1
        if (
            span_index >= len(merged)
            or not merged[span_index][0] <= offset < merged[span_index][1]
        ):
            unexpected.append(offset)
    if unexpected:
        rendered = ", ".join(f"0x{offset:X}" for offset in unexpected[:8])
        raise AssertionError(f"UI repack changed bytes outside its write envelope: {rendered}")
    return {
        "changed_bytes": changed,
        "allowed_range_count": len(merged),
        "allowed_range_bytes": sum(end - start for start, end in merged),
        "unexpected_changed_bytes": 0,
    }


def patch_relative_pointer(executable: bytearray, field_offset: int, target_offset: int) -> None:
    relative = target_offset - field_offset
    if not -(1 << 31) <= relative < (1 << 31):
        raise ValueError("self-relative pointer does not fit signed 32 bits")
    executable[field_offset:field_offset + 4] = struct.pack("<i", relative)
    if field_offset + s32(executable, field_offset) != target_offset:
        raise AssertionError("self-relative pointer verification failed")


def load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"overlay is not a JSON object: {path}")
    return document


def _walk_records(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if (
            ("source_offset" in value or "target" in value)
            and ("entry_index" in value or "sequential_index" in value or "pointer_field" in value)
            and ("korean_text" in value or "replacements" in value or "status" in value)
        ):
            yield value
            return
        for child in value.values():
            yield from _walk_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_records(child)


def overlay_records(document: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one normalised row shape for all maintained overlay schemas."""

    asset_aliases = {
        "common_preview_pool": "common_preview_and_conditions_pool",
        "common_music_demo_pool": "common_music_demo_title_pool",
        "second_ui_master": "second_ui_script_master",
    }

    if document.get("schema") == "srwcb-second-ui-names-overlay-v1":
        id_map = {
            "short_pilot_names": "pilot_short_names",
            "full_pilot_names": "pilot_full_names",
            "unit_names": "unit_names",
        }
        rows: list[dict[str, Any]] = []
        for table in document.get("tables", []):
            asset_id = id_map.get(str(table.get("id")), str(table.get("id")))
            for source in table.get("rows", []):
                korean_text = str(source.get("korean", ""))
                rows.append(
                    {
                        "asset_id": asset_id,
                        "entry_index": source["index"],
                        "pointer_field": source["pointer_field_offset"],
                        "source_offset": source["target_offset"],
                        "raw_hex": source.get("source_raw_hex"),
                        "raw_sha256": source["source_raw_sha256"],
                        "japanese_text": source.get("japanese"),
                        "korean_text": korean_text,
                        "full_korean_text": korean_text,
                        "status": "translated",
                        "review": source.get("review"),
                    }
                )
        return rows

    if document.get("schema") == "srwcb-second-ui-tables-overlay-v1":
        rows = []
        for table in document.get("tables", []):
            asset_id = str(table["asset_id"])
            for source in table.get("entries", []):
                row = dict(source)
                row["asset_id"] = asset_id
                row["entry_index"] = source["index"]
                if isinstance(source.get("korean_text"), str):
                    row["full_korean_text"] = source["korean_text"]
                # A variable-length repack fixes storage capacity, but it
                # cannot enlarge a renderer's on-screen field.  The reviewed
                # overlay therefore may carry an explicit, source-keyed
                # display-width compaction map for labels whose Japanese
                # slot is narrower than a literal Korean translation.
                compact = document.get("width_compaction", {}).get(asset_id, {})
                source_text = source.get("source_text")
                if isinstance(compact, dict) and isinstance(source_text, str):
                    display_text = compact.get(source_text)
                    if isinstance(display_text, str):
                        row["korean_text"] = display_text
                        row["display_width_compacted"] = True
                # Description records use F6 line separators.  The reviewed
                # overlay represents those separators as literal newlines;
                # rebuild_row_record still verifies the exact control
                # signature after encoding.
                if source.get("control_signature"):
                    row["allow_full_record_rebuild"] = True
                rows.append(row)
        return rows

    if document.get("schema") == "srwcb-second-ui-preview-overlay-v1":
        # Root-VM preview/condition dialogue.  Each record carries dialogue-codec
        # translation parts (control tokens preserved verbatim, Korean text runs
        # re-wrapped by the layout engine) instead of fixed span replacements.
        # The relocate=True preview path consumes translation_parts directly; the
        # plain korean_text mirror only feeds font-glyph collection.
        rows = []
        for source in document.get("records", []):
            row = dict(source)
            row["asset_id"] = str(source["asset_id"])
            row["entry_index"] = source["sequential_index"]
            rows.append(row)
        return rows

    rows = list(_walk_records(document))
    for row in rows:
        asset_id = str(row.get("asset_id", ""))
        if asset_id in asset_aliases:
            row["asset_id"] = asset_aliases[asset_id]
    if document.get("schema") == "srwcb-second-ui-scripts-overlay-v1":
        # The script overlay's own preview-pool spans preserve the retail
        # Japanese line breaks, which overflow the 20-cell window once the
        # Korean full-width text is substituted.  The dedicated preview overlay
        # supersedes them by re-wrapping through the dialogue codec, so drop the
        # legacy width-unsafe entries here to keep a single authoritative source.
        rows = [
            row
            for row in rows
            if row.get("asset_id") != "common_preview_and_conditions_pool"
        ]
    return rows


def collect_korean_ui_texts(paths: Iterable[Path]) -> list[str]:
    texts: list[str] = []
    for path in paths:
        document = load_json(path)
        for row in overlay_records(document):
            korean = row.get("korean_text")
            if isinstance(korean, str):
                texts.append(korean)
            replacements = row.get("replacements")
            if isinstance(replacements, list):
                texts.extend(
                    str(item["korean_text"])
                    for item in replacements
                    if isinstance(item, dict) and isinstance(item.get("korean_text"), str)
                )
    for replacements in globals().get("SHARED_UI_EXACT_REPLACEMENTS", {}).values():
        for item in replacements:
            for field in ("korean_text", "display_text"):
                value = item.get(field)
                if isinstance(value, str):
                    texts.append(value)
    texts.extend(MUSIC_DEMO_DISPLAY_COMPACTION_BY_INDEX.values())
    for aliases in FIXED_UI_PHASE_COMPACTION_BY_ASSET_SOURCE.values():
        texts.extend(aliases.values())
    return texts


def _record_identity(row: dict[str, Any]) -> tuple[str, int]:
    asset = str(row.get("asset_id") or row.get("table_id") or row.get("group") or "")
    if "sequential_index" in row:
        index = _int(row["sequential_index"], "sequential_index")
    else:
        index = _int(row.get("entry_index", -1), "entry_index")
    return asset, index


def _translation_map(documents: Iterable[dict[str, Any]]) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for document in documents:
        for row in overlay_records(document):
            identity = _record_identity(row)
            if not identity[0]:
                continue
            if identity in result and result[identity] != row:
                raise ValueError(f"duplicate UI overlay identity {identity}")
            result[identity] = row
    return result


def _renderer_line_advances(raw: bytes) -> tuple[int, ...]:
    """Return phase-zero advance for each F6-delimited renderer line."""

    end, tokens = parse_renderer_record(raw, 0, len(raw))
    if end != len(raw):
        raise ValueError("fixed pointer record has trailing bytes")
    lines: list[list[RendererToken]] = [[]]
    for token in tokens:
        if token.kind == "glyph":
            lines[-1].append(token)
            continue
        if token.kind == "terminator":
            continue
        if token.kind == "control" and token.raw == b"\xF6":
            lines.append([])
            continue
        raise ValueError(
            "fixed pointer record contains a non-line renderer control "
            f"{token.raw.hex(' ').upper()}"
        )
    return tuple(_renderer_span_advance(line)[0] for line in lines)


def _fixed_pointer_text_fits(
    source_raw: bytes,
    text: str,
    glyph_map: dict[str, int],
    *,
    renderer_prefix: bytes = b"",
    require_exact_layout: bool = False,
    column_capacity: int | None = None,
) -> tuple[bool, tuple[int, ...], tuple[int, ...]]:
    source_advances = _renderer_line_advances(source_raw)
    encoded = renderer_prefix + encode_ui_text(text, glyph_map, terminate=True)
    output_advances = _renderer_line_advances(encoded)
    # Some assets (the pilot/unit *name* list tables) are drawn in a fixed
    # display column whose width equals the longest retail record, not each
    # record's own advance.  For those, a Korean name may exceed its own retail
    # katakana advance as long as it still fits that shared column, so the
    # proper full name is preferred over an aggressive syllable-dropping alias.
    def _cap(source: int) -> int:
        return max(source, column_capacity) if column_capacity is not None else source
    fits = len(output_advances) == len(source_advances) and all(
        output <= _cap(source)
        for source, output in zip(source_advances, output_advances)
    )
    if fits and require_exact_layout:
        _source_end, source_tokens = parse_renderer_record(
            source_raw, 0, len(source_raw)
        )
        _output_end, output_tokens = parse_renderer_record(encoded, 0, len(encoded))
        fits = _renderer_layout_signature(output_tokens) == _renderer_layout_signature(
            source_tokens
        )
    return fits, source_advances, output_advances


def _select_fixed_pointer_overlay(
    source_raw: bytes,
    overlay: dict[str, Any],
    glyph_map: dict[str, int],
    translations: dict[tuple[str, int], dict[str, Any]],
    asset_id: str,
    index: int,
    column_capacity: int | None = None,
) -> dict[str, Any]:
    """Choose a reviewed fixed-field label using encoded renderer advance.

    These pointer records terminate independently with FF, so their final
    wide-glyph phase does not flow into a following inline field.  We still
    reject every string whose per-line advance exceeds the retail source;
    unlike the old implementation, this function never slices Korean text.
    """

    current = overlay.get("korean_text")
    if not isinstance(current, str):
        return overlay
    full = overlay.get("full_korean_text")
    if not isinstance(full, str):
        full = current
    source_text = overlay.get("japanese_text", overlay.get("source_text"))
    source_text = source_text if isinstance(source_text, str) else ""

    _source_end, source_tokens = parse_renderer_record(
        source_raw, 0, len(source_raw)
    )
    renderer_prefix = bytearray()
    for token in source_tokens:
        if token.kind == "glyph" and token.raw == RENDERER_HIGH_BLANK:
            renderer_prefix.extend(token.raw)
            continue
        break
    prefix = bytes(renderer_prefix)

    candidates: list[str] = []

    def add(value: Any) -> None:
        if isinstance(value, str):
            if prefix:
                value = value.lstrip(" \u3000")
            if value not in candidates:
                candidates.append(value)

    # Preserve the complete translation whenever it already fits.  Existing
    # table compaction, safe whitespace removal and reviewed source-keyed
    # aliases are tried in that order.
    add(full)
    add(current)
    add(_without_display_spaces(full))
    add(_without_display_spaces(current))
    add(UI_NAME_COMPACTION_BY_TEXT.get(full))
    add(
        FIXED_UI_PHASE_COMPACTION_BY_ASSET_SOURCE.get(asset_id, {}).get(
            source_text
        )
    )

    if (
        asset_id == "pilot_full_names"
        and index not in PILOT_FULL_SHORT_FALLBACK_EXCLUDED_INDICES
    ):
        short = translations.get(("pilot_short_names", index))
        if short is not None:
            short_full = short.get("full_korean_text", short.get("korean_text"))
            add(short_full)
            if isinstance(short_full, str):
                add(_without_display_spaces(short_full))
                add(UI_NAME_COMPACTION_BY_TEXT.get(short_full))
            short_source = short.get("japanese_text", short.get("source_text"))
            if isinstance(short_source, str):
                add(
                    FIXED_UI_PHASE_COMPACTION_BY_ASSET_SOURCE.get(
                        "pilot_short_names", {}
                    ).get(short_source)
                )

    last_output: tuple[int, ...] | None = None
    source_advances: tuple[int, ...] | None = None
    for candidate in candidates:
        fits, source_advances, output_advances = _fixed_pointer_text_fits(
            source_raw,
            candidate,
            glyph_map,
            renderer_prefix=prefix,
            require_exact_layout=bool(prefix),
            column_capacity=None if prefix else column_capacity,
        )
        last_output = output_advances
        if fits:
            selected = dict(overlay)
            selected["korean_text"] = candidate
            selected["display_width_compacted"] = candidate != full
            selected["display_source_text"] = source_text
            selected["source_renderer_line_advances"] = list(source_advances)
            selected["output_renderer_line_advances"] = list(output_advances)
            if prefix:
                selected["renderer_prefix_hex"] = prefix.hex(" ").upper()
            return selected

    raise ValueError(
        f"no reviewed fixed-width alias for {asset_id}[{index}] "
        f"{source_text!r} -> {full!r}; source advances={source_advances}, "
        f"last output advances={last_output}"
    )


def _mips_lui_lw_pair(register: int, address: int) -> bytes:
    if not 0 <= register < 32 or not 0 <= address <= 0xFFFFFFFF:
        raise ValueError("invalid MIPS register/address")
    high = ((address + 0x8000) >> 16) & 0xFFFF
    low = address & 0xFFFF
    lui = 0x3C000000 | (register << 16) | high
    lw = 0x8C000000 | (register << 21) | (register << 16) | low
    return struct.pack("<II", lui, lw)


def _patch_sequential_preview(
    executable: bytearray,
    inventory: dict[str, Any],
    translations: dict[tuple[str, int], dict[str, Any]],
    glyph_map: dict[str, int],
    *,
    relocate: bool = False,
) -> dict[str, Any]:
    group = inventory["common_preview_pool"]
    asset_id = str(group["asset_id"])
    pool_start = _int(group["pool_start"], "pool_start")
    pool_end = _int(group["pool_end"], "pool_end")
    capacity = pool_end - pool_start
    if not relocate:
        source_pool = bytes(executable[pool_start:pool_end])
        if len(source_pool) != _int(group["pool_bytes"], "pool_bytes"):
            raise ValueError("SECOND preview pool source changed")
        # In-place rebuild.  A reference audit found absolute pointers into
        # script 3 (0x3FB0/0x405C/0x27380/...), prefix-script relative refs into
        # script 3 (0xFB5/0x1115/0x1118/0x1138), and dozens of outward relative
        # refs from script 3 — so neither the root header, the prefix scripts,
        # nor script 3 may move (the relocate=True path hard-locks a fresh
        # SECOND boot).  Instead every record is rebuilt inside the retail
        # 3,235-byte arena and only the proven B1/B3/B4 record pointers are
        # re-aimed at the shifted record starts.  The translation must fit;
        # the build fails closed rather than truncating.
        rebuilt = bytearray()
        rebuilt_record_offsets: dict[int, int] = {}
        translated = 0
        for source_row in group["records"]:
            index = _int(source_row["sequential_index"], "sequential_index")
            raw = _verify_record_guard(executable, source_row)
            source_offset = _int(source_row["source_offset"], "source_offset")
            rebuilt_record_offsets[source_offset] = len(rebuilt)
            overlay = translations.get((asset_id, index))
            if source_row.get("translation_target") and overlay is None:
                raise ValueError(f"missing preview translation {asset_id}[{index}]")
            if overlay is not None:
                raw = rebuild_preview_record(raw, overlay, glyph_map)
                translated += 1
            rebuilt.extend(raw)
        if len(rebuilt) > capacity:
            raise ValueError(
                f"in-place preview pool needs {len(rebuilt)} bytes; "
                f"retail arena is {capacity}"
            )
        old_record_starts = set(rebuilt_record_offsets)
        pointer_patches: list[dict[str, Any]] = []
        for opcode_offset in range(ROOT_SCRIPT_ENTRY3_START, pool_start - 2):
            if executable[opcode_offset] not in (0xB1, 0xB3, 0xB4):
                continue
            operand = opcode_offset + 1
            old_target = operand + struct.unpack_from("<H", executable, operand)[0]
            if old_target not in old_record_starts:
                continue
            new_target = pool_start + rebuilt_record_offsets[old_target]
            displacement = new_target - operand
            if not 0 <= displacement <= 0xFFFF:
                raise ValueError("in-place preview pointer exceeds u16 range")
            old_disp = struct.unpack_from("<H", executable, operand)[0]
            struct.pack_into("<H", executable, operand, displacement)
            pointer_patches.append(
                {
                    "file_offset": opcode_offset,
                    "record_source_offset": old_target,
                    "source_displacement": old_disp,
                    "patched_displacement": displacement,
                    "target": new_target,
                }
            )
        executable[pool_start:pool_end] = rebuilt + b"\x00" * (
            capacity - len(rebuilt)
        )
        rebuilt_pool = bytes(executable[pool_start:pool_end])
        return {
            "asset_id": asset_id,
            "record_count": len(group["records"]),
            "translated_records": translated,
            "source_capacity": capacity,
            "rebuilt_bytes": len(rebuilt),
            "growth_bytes": 0,
            "arena_slack_bytes": capacity - len(rebuilt),
            "root_script_source_start": ROOT_SCRIPT_ENTRY3_START,
            "root_script_source_end": ROOT_SCRIPT_ENTRY3_END,
            "root_script_relocated_start": None,
            "root_script_relocated_bytes": ROOT_SCRIPT_ENTRY3_END - ROOT_SCRIPT_ENTRY3_START,
            "root_script_capacity": ROOT_SCRIPT_ENTRY3_END - ROOT_SCRIPT_ENTRY3_START,
            "root_script_slack_bytes": 0,
            "root_text_pointer_patches": pointer_patches,
            "storage": (
                "retail memory map preserved; full pool rebuilt inside the "
                "original arena with re-aimed sequential record pointers"
            ),
            "sha256": sha256(rebuilt_pool),
        }
    rebuilt = bytearray()
    rebuilt_record_offsets: dict[int, int] = {}
    translated = 0
    for source_row in group["records"]:
        index = _int(source_row["sequential_index"], "sequential_index")
        raw = _verify_record_guard(executable, source_row)
        source_offset = _int(source_row["source_offset"], "source_offset")
        rebuilt_record_offsets[source_offset] = len(rebuilt)
        overlay = translations.get((asset_id, index))
        if source_row.get("translation_target") and overlay is None:
            raise ValueError(f"missing preview translation {asset_id}[{index}]")
        if overlay is not None:
            # Dialogue preview records re-wrap Korean through the shared layout
            # codec; control tokens (F7 page, F9 name, retained glyphs) are
            # preserved verbatim and never truncated.
            raw = rebuild_preview_record(raw, overlay, glyph_map)
            translated += 1
        rebuilt.extend(raw)
    if u32(executable, ROOT_RESOURCE_HEADER) != PSX_FILE_TO_RAM_BIAS + ROOT_RESOURCE_HEADER + 4:
        raise ValueError("SECOND root resource header changed")
    if ROOT_SCRIPT_ENTRY3_FIELD + s32(executable, ROOT_SCRIPT_ENTRY3_FIELD) != ROOT_SCRIPT_ENTRY3_START:
        raise ValueError("SECOND root script 3 field changed")
    source_script = bytes(executable[ROOT_SCRIPT_ENTRY3_START:ROOT_SCRIPT_ENTRY3_END])
    if sha256(source_script) != ROOT_SCRIPT_ENTRY3_SHA256:
        raise ValueError("SECOND root script 3 source changed")
    if not ROOT_SCRIPT_ENTRY3_START < pool_start < pool_end <= ROOT_SCRIPT_ENTRY3_END:
        raise ValueError("preview pool no longer lies at the end of root script 3")

    # Script 3 must remain one contiguous VM block.  Relocate the old root
    # header/entry-prefix block to unused font slots, then grow script 3
    # backwards into the vacated 0xE14..0x1230 region.  This avoids both an EXE
    # extension and any runtime RAM reservation.
    relocated_prefix = bytearray(
        executable[ROOT_SCRIPT_ENTRY3_START:pool_start]
    )
    old_record_starts = set(rebuilt_record_offsets)
    root_pointer_patches: list[dict[str, Any]] = []
    for old_opcode in range(ROOT_SCRIPT_ENTRY3_START, pool_start - 2):
        if executable[old_opcode] not in (0xB1, 0xB3, 0xB4):
            continue
        old_operand = old_opcode + 1
        old_target = old_operand + struct.unpack_from("<H", executable, old_operand)[0]
        if old_target not in old_record_starts:
            continue
        new_opcode = old_opcode - ROOT_SCRIPT_ENTRY3_START
        new_operand = new_opcode + 1
        new_target = (
            ROOT_RESOURCE_HEADER
            + (pool_start - ROOT_SCRIPT_ENTRY3_START)
            + rebuilt_record_offsets[old_target]
        )
        displacement = new_target - (ROOT_RESOURCE_HEADER + new_operand)
        if not 0 <= displacement <= 0xFFFF:
            raise ValueError("relocated root preview pointer exceeds u16 range")
        old_disp = struct.unpack_from("<H", relocated_prefix, new_operand)[0]
        struct.pack_into("<H", relocated_prefix, new_operand, displacement)
        root_pointer_patches.append(
            {
                "file_offset": ROOT_RESOURCE_HEADER + new_opcode,
                "source_file_offset": old_opcode,
                "record_source_offset": old_target,
                "source_displacement": old_disp,
                "patched_displacement": displacement,
                "target": new_target,
            }
        )

    relocated_script = (
        bytes(relocated_prefix)
        + bytes(rebuilt)
        + bytes(executable[pool_end:ROOT_SCRIPT_ENTRY3_END])
    )
    script_capacity = ROOT_SCRIPT_ENTRY3_END - ROOT_RESOURCE_HEADER
    if len(relocated_script) > script_capacity:
        raise ValueError(
            f"rebuilt root script 3 needs {len(relocated_script)} bytes; "
            f"in-image capacity is {script_capacity}"
        )

    prefix = bytes(executable[ROOT_PREFIX_BLOCK_START:ROOT_PREFIX_BLOCK_END])
    if sha256(prefix) != ROOT_PREFIX_BLOCK_SHA256:
        raise ValueError("SECOND root prefix block changed")
    cave = bytes(executable[ROOT_RELOCATION_CAVE_START:ROOT_RELOCATION_CAVE_END])
    if sha256(cave) != ROOT_RELOCATION_CAVE_SHA256:
        raise ValueError("SECOND guarded font cave changed")
    if len(prefix) > len(cave):
        raise AssertionError("root prefix does not fit guarded font cave")
    forbidden = [
        (char, index)
        for char, index in glyph_map.items()
        if 0xA47 <= index < 0xA68
    ]
    if forbidden:
        rendered = ", ".join(f"{char!r}=0x{index:X}" for char, index in forbidden[:8])
        raise ValueError(f"font cave glyphs are assigned by final map: {rendered}")

    relocated_prefix = bytearray(prefix)
    relocated_table = PSX_FILE_TO_RAM_BIAS + ROOT_RELOCATION_CAVE_START + 4
    struct.pack_into("<I", relocated_prefix, 0, relocated_table)
    relocated_entry3_field = ROOT_RELOCATION_CAVE_START + 0x10
    entry3_relative = ROOT_RESOURCE_HEADER - relocated_entry3_field
    struct.pack_into("<i", relocated_prefix, 0x10, entry3_relative)
    executable[
        ROOT_RELOCATION_CAVE_START:
        ROOT_RELOCATION_CAVE_START + len(relocated_prefix)
    ] = relocated_prefix
    executable[
        ROOT_RELOCATION_CAVE_START + len(relocated_prefix):
        ROOT_RELOCATION_CAVE_END
    ] = b"\x00" * (len(cave) - len(relocated_prefix))

    patched_sites: list[dict[str, Any]] = []
    relocated_header_ram = PSX_FILE_TO_RAM_BIAS + ROOT_RELOCATION_CAVE_START
    for offset, register in ROOT_HEADER_LOAD_SITES:
        expected = _mips_lui_lw_pair(register, PSX_FILE_TO_RAM_BIAS + ROOT_RESOURCE_HEADER)
        if executable[offset:offset + len(expected)] != expected:
            raise ValueError(f"root-header load changed at 0x{offset:X}")
        patched = _mips_lui_lw_pair(register, relocated_header_ram)
        executable[offset:offset + len(patched)] = patched
        patched_sites.append(
            {
                "file_offset": offset,
                "register": register,
                "source": expected.hex(" ").upper(),
                "patched": patched.hex(" ").upper(),
            }
        )
    executable[ROOT_RESOURCE_HEADER:ROOT_SCRIPT_ENTRY3_END] = (
        relocated_script
        + b"\x00" * (script_capacity - len(relocated_script))
    )
    if executable[ROOT_RESOURCE_HEADER:ROOT_RESOURCE_HEADER + len(relocated_script)] != relocated_script:
        raise AssertionError("root script 3 in-image relocation failed")

    relocated_header = ROOT_RELOCATION_CAVE_START
    if u32(executable, relocated_header) != relocated_table:
        raise AssertionError("relocated root header self-pointer is wrong")
    relocated_target = relocated_entry3_field + s32(executable, relocated_entry3_field)
    if relocated_target != ROOT_RESOURCE_HEADER:
        raise AssertionError("relocated root entry 3 target is wrong")
    return {
        "asset_id": asset_id,
        "record_count": len(group["records"]),
        "translated_records": translated,
        "source_capacity": capacity,
        "rebuilt_bytes": len(rebuilt),
        "growth_bytes": len(rebuilt) - capacity,
        "root_script_source_start": ROOT_SCRIPT_ENTRY3_START,
        "root_script_source_end": ROOT_SCRIPT_ENTRY3_END,
        "root_script_relocated_start": ROOT_RESOURCE_HEADER,
        "root_script_relocated_bytes": len(relocated_script),
        "root_script_capacity": script_capacity,
        "root_script_slack_bytes": script_capacity - len(relocated_script),
        "root_header_relocated_start": ROOT_RELOCATION_CAVE_START,
        "root_header_relocated_end": ROOT_RELOCATION_CAVE_START + len(relocated_prefix),
        "root_header_load_patches": patched_sites,
        "root_pointer_field": relocated_entry3_field,
        "root_text_pointer_patches": root_pointer_patches,
        "storage": "complete root VM script 3 repacked into vacated retail-image prefix",
        "sha256": sha256(bytes(rebuilt)),
    }


def _rebuild_music_demo_title(
    source: bytes,
    overlay: dict[str, Any],
    glyph_map: dict[str, int],
    index: int,
) -> bytes:
    """Rebuild one title without changing the list renderer's cursor phase.

    Hiragana and katakana in the retail font mostly use low one-byte glyphs,
    while Korean syllables use high two-byte glyphs.  A plain text rebuild can
    therefore finish at a different cursor position and wide-glyph phase even
    when both strings contain the same number of characters.  The music/demo
    list is a fixed-layout consumer, so preserve the complete stateful layout
    signature and use a reviewed shorter display alias only when necessary.
    """

    end, source_tokens = parse_renderer_record(source, 0, len(source))
    if end != len(source):
        raise ValueError(f"music/demo source [{index}] has trailing bytes")
    if any(token.kind == "control" for token in source_tokens):
        raise ValueError(f"music/demo source [{index}] unexpectedly has controls")
    source_glyphs = [token for token in source_tokens if token.kind == "glyph"]
    korean = overlay.get("korean_text")
    if not isinstance(korean, str):
        replacements = overlay.get("replacements")
        if isinstance(replacements, list) and not replacements and not source_glyphs:
            return source
        if not isinstance(replacements, list) or len(replacements) != 1:
            raise ValueError(f"music/demo translation [{index}] is not one text span")
        korean = replacements[0].get("korean_text")
    if not isinstance(korean, str):
        raise ValueError(f"music/demo translation [{index}] has no Korean text")
    display_text = MUSIC_DEMO_DISPLAY_COMPACTION_BY_INDEX.get(index, korean)
    encoded = _encode_fixed_span_text(
        display_text,
        glyph_map,
        len(source_glyphs),
        preserve_width=True,
        renderer_layout=_renderer_layout_signature(source_glyphs),
        require_matching_phase=True,
    )
    rebuilt = encoded + b"\xFF"
    rebuilt_end, rebuilt_tokens = parse_renderer_record(rebuilt, 0, len(rebuilt))
    if rebuilt_end != len(rebuilt):
        raise AssertionError(f"music/demo output [{index}] has trailing bytes")
    if _renderer_layout_signature(rebuilt_tokens) != _renderer_layout_signature(
        source_tokens
    ):
        raise AssertionError(f"music/demo output [{index}] changed renderer layout")
    return rebuilt


def _prepare_sequential_music_pool(
    executable: bytearray,
    inventory: dict[str, Any],
    translations: dict[tuple[str, int], dict[str, Any]],
    glyph_map: dict[str, int],
) -> tuple[bytes, list[tuple[int, int]], dict[str, Any]]:
    group = inventory["common_music_demo_pool"]
    asset_id = str(group["asset_id"])
    rebuilt_pool = bytearray()
    old_to_relative: dict[int, int] = {}
    translated = 0
    for source_row in group["records"]:
        index = _int(source_row["sequential_index"], "sequential_index")
        source_offset = _int(source_row["source_offset"], "source_offset")
        raw = _verify_record_guard(executable, source_row)
        overlay = translations.get((asset_id, index))
        if source_row.get("translation_target") and overlay is None:
            raise ValueError(f"missing music/demo translation {asset_id}[{index}]")
        if overlay is not None:
            raw = _rebuild_music_demo_title(raw, overlay, glyph_map, index)
            translated += 1
        old_to_relative[source_offset] = len(rebuilt_pool)
        rebuilt_pool.extend(raw)

    pointer_count = 0
    pointer_updates: list[tuple[int, int]] = []
    for source_row in group["records"]:
        relative_target = old_to_relative[_int(source_row["source_offset"], "source_offset")]
        for pointer_field in source_row.get("pointer_fields", []):
            field = _int(pointer_field, "pointer_field")
            expected_source = _int(source_row["source_offset"], "source_offset")
            if field + s32(executable, field) != expected_source:
                raise ValueError(
                    f"music/demo pointer 0x{field:X} no longer targets "
                    f"0x{expected_source:X}"
                )
            pointer_updates.append((field, relative_target))
            pointer_count += 1
    if pointer_count != _int(group["nested_entry_count"], "nested_entry_count"):
        raise ValueError(
            f"music/demo pointer coverage {pointer_count} != {group['nested_entry_count']}"
        )
    return bytes(rebuilt_pool), pointer_updates, {
        "asset_id": asset_id,
        "record_count": len(group["records"]),
        "translated_records": translated,
        "stateful_layout_preserved_records": translated,
        "compacted_display_records": sum(
            index in MUSIC_DEMO_DISPLAY_COMPACTION_BY_INDEX
            for index in range(len(group["records"]))
        ),
        "pointer_count": pointer_count,
        "sequential_only_records": _int(group["sequential_only_record_count"], "sequential_only_record_count"),
        "rebuilt_bytes": len(rebuilt_pool),
        "storage": "contiguous pool in guarded original record spans",
    }


def _prepare_pointer_group(
    executable: bytearray,
    asset_id: str,
    source_rows: list[dict[str, Any]],
    translations: dict[tuple[str, int], dict[str, Any]],
    glyph_map: dict[str, int],
    *,
    require_all_targets: bool,
    repack_untranslated: bool = True,
) -> tuple[list[dict[str, Any]], list[tuple[int, int]], dict[str, Any]]:
    translated = 0
    display_width_compacted = 0
    fixed_renderer_width_validated = 0
    renderer_prefix_preserved = 0
    planned: list[dict[str, Any]] = []
    source_spans: list[tuple[int, int]] = []
    grammar = (
        SECOND_UI_VM_GRAMMAR
        if asset_id == "second_ui_script_master"
        else RENDERER_GRAMMAR
    )
    column_capacity: int | None = None
    if asset_id in RELAXED_COLUMN_NAME_ASSETS:
        record_advances: list[int] = []
        for candidate_row in source_rows:
            raw_hex = candidate_row.get("raw_hex") or candidate_row.get("source_hex")
            if not raw_hex:
                continue
            advances = _renderer_line_advances(bytes.fromhex(str(raw_hex)))
            if advances:
                record_advances.append(max(advances))
        if record_advances:
            column_capacity = max(record_advances)
    for source_row in source_rows:
        index = _int(source_row["entry_index"], "entry_index")
        overlay = translations.get((asset_id, index))
        required = bool(source_row.get("translation_target")) and require_all_targets
        if required and overlay is None:
            raise ValueError(f"missing table translation {asset_id}[{index}]")
        if overlay is None and not repack_untranslated:
            continue

        classification = str(source_row.get("classification", ""))
        guarded = any(
            source_row.get(field) is not None
            for field in ("raw_hex", "source_hex", "raw_sha256", "source_sha256")
        )
        if classification == "non_ff_terminated_target" or not guarded:
            if overlay is not None:
                raise ValueError(f"overlay targets structural table entry {asset_id}[{index}]")
            continue

        source_offset = _int(source_row.get("source_offset", source_row.get("target")), "source_offset")
        source_raw = _verify_record_guard(executable, source_row, grammar=grammar)
        if not source_raw.endswith(b"\xFF"):
            raise ValueError(f"guarded UI record at 0x{source_offset:X} has no FF terminator")
        if overlay is not None and asset_id in FIXED_POINTER_TEXT_ASSETS:
            try:
                overlay = _select_fixed_pointer_overlay(
                    source_raw,
                    overlay,
                    glyph_map,
                    translations,
                    asset_id,
                    index,
                    column_capacity=column_capacity,
                )
            except ValueError as exc:
                raise ValueError(f"{asset_id}[{index}]: {exc}") from exc
            display_width_compacted += int(
                bool(overlay.get("display_width_compacted"))
            )
            fixed_renderer_width_validated += 1
            renderer_prefix_preserved += int(
                bool(overlay.get("renderer_prefix_hex"))
            )
        if grammar == SECOND_UI_VM_GRAMMAR:
            inferred = _infer_ui_text_replacements(
                source_raw,
                list((overlay or {}).get("replacements") or []),
            )
            if inferred:
                overlay = dict(overlay or {})
                overlay["replacements"] = list(overlay.get("replacements") or []) + inferred
        try:
            rebuilt = (
                rebuild_row_record(source_raw, overlay, glyph_map, grammar=grammar)
                if overlay is not None
                else source_raw
            )
        except ValueError as exc:
            raise ValueError(f"{asset_id}[{index}]: {exc}") from exc
        field_value = (
            overlay.get("pointer_field", source_row.get("pointer_field"))
            if overlay is not None
            else source_row.get("pointer_field")
        )
        if field_value is None:
            raise ValueError(f"{asset_id}[{index}] has no pointer field")
        field = _int(field_value, "pointer_field")
        if field + s32(executable, field) != source_offset:
            raise ValueError(
                f"{asset_id}[{index}] pointer 0x{field:X} no longer targets "
                f"guarded source 0x{source_offset:X}"
            )
        planned.append(
            {
                "asset_id": asset_id,
                "entry_index": index,
                "pointer_field": field,
                "source_offset": source_offset,
                "raw": rebuilt,
                "translated": overlay is not None,
                "display_width_compacted": bool(
                    overlay is not None
                    and overlay.get("display_width_compacted")
                ),
                "renderer_prefix_preserved": bool(
                    overlay is not None and overlay.get("renderer_prefix_hex")
                ),
            }
        )
        source_spans.append((source_offset, source_offset + len(source_raw)))
        if overlay is not None:
            translated += 1
    return planned, source_spans, {
        "asset_id": asset_id,
        "source_entries": len(source_rows),
        "translated_entries": translated,
        "display_width_compacted_entries": display_width_compacted,
        "fixed_renderer_width_validated_entries": fixed_renderer_width_validated,
        "renderer_prefix_preserved_entries": renderer_prefix_preserved,
        "guarded_record_entries": len(planned),
        "unique_rebuilt_records": len({row["raw"] for row in planned}),
        "patched_pointers": len(planned),
    }


def _inventory_table_map(inventory: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result = {
        str(table["asset_id"]): table
        for table in inventory["other_menu_visible_tables"]
    }
    result[str(inventory["second_ui_master"]["asset_id"])] = inventory["second_ui_master"]
    return result


def _prepare_overlay_only_group(
    executable: bytearray,
    asset_id: str,
    rows: list[dict[str, Any]],
    glyph_map: dict[str, int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    in_place: list[dict[str, Any]] = []
    relocated: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: _record_identity(item)[1]):
        _asset, index = _record_identity(row)
        source_offset = _int(row.get("source_offset", row.get("target")), "source_offset")
        source_raw = _verify_record_guard(executable, row)
        rebuilt = rebuild_row_record(source_raw, row, glyph_map)
        field_value = row.get("pointer_field")
        if field_value is None:
            raise ValueError(f"{asset_id}[{index}] has no pointer field")
        field = _int(field_value, "pointer_field")
        if field + s32(executable, field) != source_offset:
            raise ValueError(
                f"{asset_id}[{index}] pointer 0x{field:X} no longer targets "
                f"0x{source_offset:X}"
            )
        plan = {
            "asset_id": asset_id,
            "entry_index": index,
            "pointer_field": field,
            "source_offset": source_offset,
            "source_size": len(source_raw),
            "raw": rebuilt,
            "translated": True,
        }
        if len(rebuilt) == len(source_raw):
            in_place.append(plan)
        else:
            relocated.append(plan)
    return in_place, relocated, {
        "asset_id": asset_id,
        "source_entries": len(rows),
        "translated_entries": len(rows),
        "in_place_entries": len(in_place),
        "relocated_entries": len(relocated),
        "patched_pointers": len(rows),
    }


def _patch_common_audio_option_width(executable: bytearray) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for offset, source, patched in COMMON_AUDIO_OPTION_WIDTH_PATCHES:
        end = offset + len(source)
        if executable[offset:end] != source:
            raise ValueError(
                f"common audio option-width instruction changed at 0x{offset:X}"
            )
        executable[offset:end] = patched
        rows.append(
            {
                "file_offset": offset,
                "source": source.hex(" ").upper(),
                "patched": patched.hex(" ").upper(),
            }
        )
    return {
        "asset_id": "common_ui_option_width_code",
        "patched_call_sites": len(rows),
        "source_group_bytes": 4,
        "patched_group_bytes": 4,
        "patches": rows,
    }


def patch_second_executable_ui(
    executable_root: Path,
    glyph_map: dict[str, int],
    inventory_path: Path,
    overlay_paths: Iterable[Path],
    label_overlay_path: Path | None = None,
) -> dict[str, Any]:
    """Apply all approved SECOND menu overlays without extending SECOND.WAR."""

    inventory_raw = inventory_path.read_bytes()
    inventory = json.loads(inventory_raw.decode("utf-8"))
    documents = [load_json(Path(path)) for path in overlay_paths]
    translations = _translation_map(documents)

    executable_path = executable_root / SECOND_EXECUTABLE
    executable = bytearray(executable_path.read_bytes())
    if executable[:8] != PSX_EXE_MAGIC:
        raise ValueError("SECOND.WAR is not a PS-X EXE")
    if len(executable) != RETAIL_FILE_BYTES or u32(executable, 0x1C) != RETAIL_T_SIZE:
        raise ValueError("SECOND.WAR must have its retail image size before UI repack")
    source_executable = bytes(executable)

    music_raw, music_pointer_updates, music_manifest = _prepare_sequential_music_pool(
        executable, inventory, translations, glyph_map
    )

    table_map = _inventory_table_map(inventory)
    pointer_plans: list[dict[str, Any]] = []
    source_spans: list[tuple[int, int]] = []
    donor_record_spans: list[tuple[int, int]] = []
    table_manifests: list[dict[str, Any]] = []
    for asset_id, table in table_map.items():
        planned, spans, manifest = _prepare_pointer_group(
            executable,
            asset_id,
            list(table["records"]),
            translations,
            glyph_map,
            require_all_targets=True,
            # Repack the complete UI master so uncovered common labels (for
            # example 지도 보기 and chapter-selection text) can be filled by
            # the source-text fallback below; records without a matching
            # translation remain byte-identical.
            repack_untranslated=True,
        )
        pointer_plans.extend(planned)
        if asset_id != str(inventory["second_ui_master"]["asset_id"]):
            source_spans.extend(spans)
            donor_record_spans.extend(spans)
        table_manifests.append(manifest)

    music_group = inventory["common_music_demo_pool"]
    music_source_start = _int(music_group["pool_start"], "pool_start")
    music_source_end = _int(music_group["pool_end"], "pool_end")
    if music_source_end - music_source_start != _int(music_group["pool_bytes"], "pool_bytes"):
        raise ValueError("music/demo source-pool bounds changed")
    source_spans.append((music_source_start, music_source_end))
    for source_row in music_group["records"]:
        source_offset = _int(source_row["source_offset"], "source_offset")
        source_raw = _verify_record_guard(executable, source_row)
        donor_record_spans.append(
            (source_offset, source_offset + len(source_raw))
        )

    # Common-master overlay rows are a mixed resource, not a reclaimable
    # string table.  Equal-size records stay in place; only growing rows use
    # the guarded donor spans collected from complete pointer-backed tables.
    known_assets = set(table_map) | {
        str(inventory["common_preview_pool"]["asset_id"]),
        str(inventory["common_music_demo_pool"]["asset_id"]),
    }
    overlay_only: dict[str, list[dict[str, Any]]] = {}
    for row in translations.values():
        asset_id, _index = _record_identity(row)
        if asset_id in known_assets:
            continue
        overlay_only.setdefault(asset_id, []).append(row)
    common_in_place: list[dict[str, Any]] = []
    common_relocated: list[dict[str, Any]] = []
    common_manifests: list[dict[str, Any]] = []
    for asset_id, rows in sorted(overlay_only.items()):
        in_place, relocated, manifest = _prepare_overlay_only_group(
            executable, asset_id, rows, glyph_map
        )
        common_in_place.extend(in_place)
        common_relocated.extend(relocated)
        common_manifests.append(manifest)

    # Audit every final inventory-backed UI record before borrowing font
    # pixels.  Translated master rows are in pointer_plans; untranslated
    # master rows remain at their guarded source locations and must be checked
    # separately.  Preview/music records are sequential rather than leaf-
    # pointer plans, so materialise/parse those streams here as well.
    final_ui_tail_glyphs: set[int] = set()
    for row in pointer_plans:
        grammar = (
            SECOND_UI_VM_GRAMMAR
            if row["asset_id"] == "second_ui_script_master"
            else RENDERER_GRAMMAR
        )
        final_ui_tail_glyphs.update(
            _font_tail_glyph_references(bytes(row["raw"]), grammar=grammar)
        )
    planned_master_indices = {
        int(row["entry_index"])
        for row in pointer_plans
        if row["asset_id"] == "second_ui_script_master"
    }
    for source_row in inventory["second_ui_master"]["records"]:
        index = _int(source_row["entry_index"], "entry_index")
        if index in planned_master_indices:
            continue
        classification = str(source_row.get("classification", ""))
        guarded = any(
            source_row.get(field) is not None
            for field in ("raw_hex", "source_hex", "raw_sha256", "source_sha256")
        )
        if classification == "non_ff_terminated_target" or not guarded:
            continue
        final_ui_tail_glyphs.update(
            _font_tail_glyph_references(
                _verify_record_guard(
                    executable,
                    source_row,
                    grammar=SECOND_UI_VM_GRAMMAR,
                ),
                grammar=SECOND_UI_VM_GRAMMAR,
            )
        )
    final_ui_tail_glyphs.update(
        _font_tail_glyph_references(
            music_raw,
            grammar=RENDERER_GRAMMAR,
            stream=True,
        )
    )
    preview_group = inventory["common_preview_pool"]
    preview_asset_id = str(preview_group["asset_id"])
    for source_row in preview_group["records"]:
        index = _int(source_row["sequential_index"], "sequential_index")
        raw = _verify_record_guard(executable, source_row)
        overlay = translations.get((preview_asset_id, index))
        if overlay is not None:
            raw = rebuild_preview_record(raw, overlay, glyph_map)
        final_ui_tail_glyphs.update(
            _font_tail_glyph_references(raw, grammar=RENDERER_GRAMMAR)
        )
    for row in common_in_place + common_relocated:
        final_ui_tail_glyphs.update(
            _font_tail_glyph_references(bytes(row["raw"]), grammar=RENDERER_GRAMMAR)
        )
    unpreserved_tail_glyphs = final_ui_tail_glyphs - set(
        STATIC_FONT_DONOR_EXCLUDED_GLYPHS
    )
    if unpreserved_tail_glyphs:
        rendered = ", ".join(
            f"0x{index:X}" for index in sorted(unpreserved_tail_glyphs)
        )
        raise ValueError(f"final UI still references static font-donor glyphs: {rendered}")

    pointer_fields = {
        int(row["pointer_field"])
        for row in pointer_plans
    } | {
        int(row["pointer_field"])
        for row in common_in_place + common_relocated
    } | {field for field, _relative in music_pointer_updates}

    safe_source_spans, protected_donor_snapshots, hidden_reference_guard = (
        _guard_untracked_donor_references(
            executable,
            source_spans,
            donor_record_spans,
            pointer_fields,
        )
    )
    static_font_donors = _guard_static_font_donor(executable, glyph_map)
    pool = StaticPool([*safe_source_spans, *static_font_donors])
    for field in pointer_fields:
        if any(max(start, field) < min(end, field + 4) for start, end in pool.spans):
            raise ValueError(f"static donor span overlaps pointer field 0x{field:X}")

    music_start = pool.add(
        music_raw,
        asset_id=str(inventory["common_music_demo_pool"]["asset_id"]),
        key="complete_ordered_pool",
        intern=False,
        alignment=4,
    )
    music_manifest["pool_start"] = music_start
    music_manifest["pool_end"] = music_start + len(music_raw)

    unique_plans: dict[bytes, dict[str, Any]] = {}
    for row in pointer_plans + common_relocated:
        unique_plans.setdefault(bytes(row["raw"]), row)
    target_by_raw: dict[bytes, int] = {}
    for raw, representative in sorted(
        unique_plans.items(), key=lambda item: (-len(item[0]), item[0])
    ):
        target_by_raw[raw] = pool.add(
            raw,
            asset_id=str(representative["asset_id"]),
            key=f"record[{representative['entry_index']}]",
            intern=True,
        )

    pool.commit(executable)
    for field, relative_target in music_pointer_updates:
        patch_relative_pointer(executable, field, music_start + relative_target)
    for row in pointer_plans + common_relocated:
        target = target_by_raw[bytes(row["raw"])]
        patch_relative_pointer(executable, int(row["pointer_field"]), target)
    for row in common_in_place:
        target = int(row["source_offset"])
        raw = bytes(row["raw"])
        executable[target:target + len(raw)] = raw
        patch_relative_pointer(executable, int(row["pointer_field"]), target)

    for row in pointer_plans + common_relocated + common_in_place:
        field = int(row["pointer_field"])
        target = field + s32(executable, field)
        raw = bytes(row["raw"])
        if executable[target:target + len(raw)] != raw:
            raise AssertionError(
                f"repacked UI pointer verification failed for "
                f"{row['asset_id']}[{row['entry_index']}]"
            )

    groups: list[dict[str, Any]] = []
    # The preview/condition dialogue is rebuilt IN PLACE.  A binary reference
    # audit (2026-07-21) found unpatched references that make any root-VM move
    # hard-lock a fresh SECOND boot: absolute pointers into script 3 at
    # 0x3FB0/0x405C/0x27380/0x2BD00/0x2D24C/0x2F4F0, prefix-script relative
    # refs into script 3 at 0xFB5/0x1115/0x1118/0x1138, and ~25 outward
    # relative refs from script 3 code.  relocate=True is therefore unsafe;
    # the Korean pool must fit the retail 3,235-byte arena instead.
    groups.append(
        _patch_sequential_preview(
            executable, inventory, translations, glyph_map, relocate=False
        )
    )
    groups.append(music_manifest)
    groups.extend(table_manifests)
    groups.extend(common_manifests)

    groups.append(_patch_common_audio_option_width(executable))
    save_prompt_spans, save_prompt_manifest = _patch_common_save_prompt_records(
        executable, glyph_map
    )
    groups.append(save_prompt_manifest)

    label_spans: list[tuple[int, int]] = []
    if label_overlay_path is not None:
        label_spans, label_manifest = patch_map_label_heap(
            executable, label_overlay_path, glyph_map
        )
        groups.append(label_manifest)

    # Recheck after root relocation and code edits, not merely after the pool
    # commit.  This makes write ordering fail closed if a future layout change
    # causes either late patch to overlap a pointer field or payload.
    if executable[music_start:music_start + len(music_raw)] != music_raw:
        raise AssertionError("late UI patch overwrote the rebuilt music/demo pool")
    for field, relative_target in music_pointer_updates:
        if field + s32(executable, field) != music_start + relative_target:
            raise AssertionError(f"late UI patch changed music/demo pointer 0x{field:X}")
    for row in pointer_plans + common_relocated + common_in_place:
        field = int(row["pointer_field"])
        target = field + s32(executable, field)
        raw = bytes(row["raw"])
        if executable[target:target + len(raw)] != raw:
            raise AssertionError(
                f"late UI patch changed {row['asset_id']}[{row['entry_index']}]"
            )

    for start, end, source in protected_donor_snapshots:
        if executable[start:end] != source:
            raise AssertionError(
                f"hidden-reference source span 0x{start:X}..0x{end:X} was overwritten"
            )

    change_audit = _audit_allowed_changes(
        source_executable,
        executable,
        [
            *(
                (offset, offset + len(raw))
                for offset, raw in pool.payloads.items()
            ),
            *((field, field + 4) for field in pointer_fields),
            *(
                (
                    int(row["source_offset"]),
                    int(row["source_offset"]) + len(bytes(row["raw"])),
                )
                for row in common_in_place
            ),
            (ROOT_RESOURCE_HEADER, ROOT_SCRIPT_ENTRY3_END),
            (ROOT_RELOCATION_CAVE_START, ROOT_RELOCATION_CAVE_END),
            *((offset, offset + 8) for offset, _register in ROOT_HEADER_LOAD_SITES),
            *(
                (offset, offset + len(source))
                for offset, source, _patched in COMMON_AUDIO_OPTION_WIDTH_PATCHES
            ),
            *save_prompt_spans,
            *label_spans,
        ],
    )

    if len(executable) != RETAIL_FILE_BYTES or u32(executable, 0x1C) != RETAIL_T_SIZE:
        raise AssertionError("UI repack changed SECOND.WAR's retail load boundary")

    executable_path.write_bytes(executable)
    return {
        "format": "srwcb-second-executable-ui-static-repack-v2",
        "path": str(SECOND_EXECUTABLE).replace("\\", "/"),
        "inventory": {
            "path": str(inventory_path),
            "sha256": sha256(inventory_raw),
        },
        "overlays": [
            {"path": str(path), "sha256": sha256(Path(path).read_bytes())}
            for path in overlay_paths
        ],
        "storage": "guarded original record spans and unassigned font tail; no post-BSS arena",
        "hidden_relative_reference_guard": hidden_reference_guard,
        "static_font_donor": {
            "file_start": STATIC_FONT_DONOR_START,
            "file_end": STATIC_FONT_DONOR_END,
            "glyph_start": STATIC_FONT_DONOR_GLYPH_START,
            "glyph_end_exclusive": STATIC_FONT_DONOR_GLYPH_END,
            "excluded_glyphs": list(STATIC_FONT_DONOR_EXCLUDED_GLYPHS),
            "final_ui_tail_glyph_references": sorted(final_ui_tail_glyphs),
            "usable_spans": static_font_donors,
            "source_sha256": STATIC_FONT_DONOR_SHA256,
        },
        "change_audit": change_audit,
        "source_span_capacity": pool.capacity,
        "allocated_payload_bytes": pool.used,
        "remaining_source_span_bytes": pool.capacity - pool.used,
        "new_file_bytes": len(executable),
        "new_t_size": u32(executable, 0x1C),
        "new_loaded_end_ram": PSX_FILE_TO_RAM_BIAS + len(executable),
        "groups": groups,
        "allocations": pool.allocations,
        "executable_sha256_before_runtime_boundary_patch": sha256(bytes(executable)),
    }


# SLPS_020.70 owns the front-end, save/load, option, and other shared menu
# resources used before/around each of the four games.  Its UI master uses the
# same stateful VM grammar as SECOND.WAR, but its pointer table and record
# offsets are different.  Keep this small repacker separate from the SECOND
# battle executable patch: it only moves the 107 shared UI records and leaves
# the retail executable size and load boundary unchanged.
SHARED_UI_MASTER_HEADER = 0x1A154
SHARED_UI_MASTER_BOUND = 0x1D168
SHARED_FONT_BASE = 0x1EDB8
SHARED_GLYPH_BYTES = 32
SHARED_FONT_DONOR_GLYPH_START = 0xA68
SHARED_FONT_DONOR_GLYPH_END = 0xB00
SHARED_FONT_DONOR_SHA256 = (
    "d5993c29f25d93133c3f4e2a3b65a7a727282f6abc5d3ebc8538fa550a0b44c1"
)
# These are referenced by the shared preview/UI resources in the retail
# executable.  They remain intact while the other unassigned tail glyphs are
# used as guarded static storage for the few bytes of menu growth.
SHARED_FONT_DONOR_EXCLUDED_GLYPHS = (
    0xA75,
    0xA76,
    0xAA7,
    0xAC3,
    0xAF1,
    0xAFA,
    0xAFB,
)

SHARED_COMMON_MASTER_FIELD_BASE = 0x9714
SHARED_MUSIC_POINTER_TABLE = 0x9F85
SHARED_MUSIC_POINTER_COUNT = 172
SHARED_MUSIC_SOURCE_START = 0xBA36
SHARED_MUSIC_HIDDEN_POINTERS = {0x3E40: 121}
SHARED_PREVIEW_SOURCE_START = 0x5D62

# SLPS-only master rows and source variants that do not exist byte-for-byte in
# SECOND.WAR.  Every span is guarded by its pristine source bytes; the common
# stateful encoder below supplies the exact renderer padding.
SHARED_UI_EXACT_REPLACEMENTS: dict[int, list[dict[str, Any]]] = {
    11: [
        {"relative_start": 19, "relative_end": 27, "source_hex": "C4 96 93 A9 EC E4 EC E5", "japanese_text": "フェイズ終了", "korean_text": "페이즈 종료"},
        {"relative_start": 28, "relative_end": 34, "source_hex": "EC E6 EC E7 EC E8", "japanese_text": "部隊表", "korean_text": "부대 목록"},
        {"relative_start": 35, "relative_end": 43, "source_hex": "EC 65 EC 55 EC 57 EC 42", "japanese_text": "反撃命令", "korean_text": "반격 명령"},
        {"relative_start": 44, "relative_end": 52, "source_hex": "EC 17 EC 52 ED 30 ED 0C", "japanese_text": "作戦目的", "korean_text": "작전 목적"},
        {"relative_start": 53, "relative_end": 61, "source_hex": "EC 78 EC 06 EF 9D EF 21", "japanese_text": "精神検索", "korean_text": "정신기 검색"},
        {"relative_start": 62, "relative_end": 66, "source_hex": "A6 A8 B5 CF", "japanese_text": "システム", "korean_text": "시스템"},
        {"relative_start": 67, "relative_end": 71, "source_hex": "EC E4 EC E5", "japanese_text": "終了", "korean_text": "종료"},
        {"relative_start": 84, "relative_end": 89, "source_hex": "AE 11 DF EC EC", "japanese_text": "タ-ン数", "korean_text": "턴 수"},
        {"relative_start": 90, "relative_end": 94, "source_hex": "ED F1 EC F3", "japanese_text": "資金", "korean_text": "자금"},
    ],
    39: [
        {"relative_start": 20, "relative_end": 28, "source_hex": "A6 A8 B5 CF EE 65 ED 06", "japanese_text": "システム設定", "korean_text": "시스템 설정"},
        {"relative_start": 32, "relative_end": 36, "source_hex": "A8 9E 97 91", "japanese_text": "スクエア", "korean_text": "이동 범위"},
        {"relative_start": 37, "relative_end": 41, "source_hex": "A4 95 DF B8", "japanese_text": "サウンド", "korean_text": "사운드"},
        {"relative_start": 42, "relative_end": 53, "source_hex": "EC 52 EC 53 17 1C 22 EE 65 ED 06", "japanese_text": "戦闘BGM設定", "korean_text": "전투 BGM 설정"},
        {"relative_start": 72, "relative_end": 80, "source_hex": "ED 67 EF 9A EC F0 EC 17", "japanese_text": "特殊操作", "korean_text": "특수 조작"},
        {"relative_start": 95, "relative_end": 110, "source_hex": "10 AA DB 9E B7 10 A8 AE 11 B7 66 D9 AA B2 B7", "japanese_text": "+セレクト+スタ-トでリセット", "korean_text": "+선택+시작으로 리셋", "display_text": "+선+시작리셋"},
        {"relative_start": 113, "relative_end": 120, "source_hex": "CB AE DF EE 65 ED 06", "japanese_text": "ボタン設定", "korean_text": "버튼 설정"},
        {"relative_start": 124, "relative_end": 128, "source_hex": "ED 05 ED 06", "japanese_text": "決定", "korean_text": "결정"},
        {"relative_start": 129, "relative_end": 134, "source_hex": "9C D2 DF AA DA", "japanese_text": "キャンセル", "korean_text": "취소"},
        {"relative_start": 135, "relative_end": 142, "source_hex": "A8 C3 11 B8 91 B2 C6", "japanese_text": "スピ-ドアップ", "korean_text": "속도 올리기"},
        {"relative_start": 143, "relative_end": 150, "source_hex": "EC E9 EC 74 CD B2 C6", "japanese_text": "全体マップ", "korean_text": "전체 지도"},
        {"relative_start": 151, "relative_end": 157, "source_hex": "EC E6 EC E7 EC E8", "japanese_text": "部隊表", "korean_text": "부대 목록"},
        {"relative_start": 158, "relative_end": 169, "source_hex": "EE 36 EC A5 D5 BA B2 B7 EE 1B 89", "japanese_text": "自軍ユニット送り", "korean_text": "아군 유닛 다음"},
        {"relative_start": 170, "relative_end": 181, "source_hex": "EE 36 EC A5 D5 BA B2 B7 ED DC 56", "japanese_text": "自軍ユニット戻し", "korean_text": "아군 유닛 이전"},
        {"relative_start": 182, "relative_end": 193, "source_hex": "EC 93 EC A5 D5 BA B2 B7 EE 1B 89", "japanese_text": "敵軍ユニット送り", "korean_text": "적군 유닛 다음"},
        {"relative_start": 194, "relative_end": 205, "source_hex": "EC 93 EC A5 D5 BA B2 B7 ED DC 56", "japanese_text": "敵軍ユニット戻し", "korean_text": "적군 유닛 이전"},
        {"relative_start": 206, "relative_end": 218, "source_hex": "EE C8 ED 84 EE 65 ED 06 6A ED DC 58", "japanese_text": "初期設定に戻す", "korean_text": "초기 설정으로"},
    ],
    60: [
        {"relative_start": 72, "relative_end": 76, "source_hex": "EE 4F EC 78", "japanese_text": "妖精", "korean_text": "요정"},
    ],
    64: [
        {"relative_start": 20, "relative_end": 32, "source_hex": "D5 BA B2 B7 ED 67 ED 07 CB 11 B9 A8", "japanese_text": "ユニット特別ボ-ナス", "korean_text": "유닛 특별 보너스"},
        {"relative_start": 35, "relative_end": 74, "source_hex": "EC E9 65 6D C0 D8 D0 11 AE 8E EC 5E EC 16 7D 66 EC 4A EE AA 56 5E 6D 66 3A ED 67 ED 07 CB 11 B9 A8 4B 63 4C 7D 58 E4", "japanese_text": "全てのパラメ-タを最大まで改造したので,特別ボ-ナスがつきます。", "korean_text": "모든 능력치를 최대로 개조하여 특별 보너스를 받습니다."},
        {"relative_start": 75, "relative_end": 99, "source_hex": "ED 0D ED 0E 6D EC 58 4A 88 31 63 5F 50 ED 23 ED 24 56 65 ED 0E 54 43 E4", "japanese_text": "以下の中から1つだけ選択して下さい。", "korean_text": "다음 중 하나만 선택해 주세요."},
        {"relative_start": 103, "relative_end": 135, "source_hex": "EC B6 3B EC F1 3B EC 4F 3B F0 0D 6D 43 59 8B 4A 6D EC 6F EC 81 EC FF EC 66 8E 16 6A 66 4C 8A E4", "japanese_text": "空.陸.海.宇のいずれかの地形適応をAにできる。", "korean_text": "공·육·해·우 중 하나를 A로"},
        {"relative_start": 136, "relative_end": 150, "source_hex": "D5 BA B2 B7 6D EC 50 EC 51 EC 2C 10 32 E4", "japanese_text": "ユニットの移動力+2。", "korean_text": "이동력 +2."},
        {"relative_start": 151, "relative_end": 164, "source_hex": "D5 BA B2 B7 6D 1D 25 10 31 35 30 30 E4", "japanese_text": "ユニットのHP+1500。", "korean_text": "HP +1500."},
        {"relative_start": 165, "relative_end": 177, "source_hex": "D5 BA B2 B7 6D 1A 23 10 31 30 30 E4", "japanese_text": "ユニットのEN+100。", "korean_text": "EN +100."},
        {"relative_start": 178, "relative_end": 193, "source_hex": "D5 BA B2 B7 6D ED A6 EC 51 ED 01 10 35 30 E4", "japanese_text": "ユニットの運動性+50。", "korean_text": "운동성 +50."},
        {"relative_start": 194, "relative_end": 208, "source_hex": "D5 BA B2 B7 6D EC 62 EC 01 10 36 30 30 E4", "japanese_text": "ユニットの装甲+600。", "korean_text": "장갑 +600."},
        {"relative_start": 220, "relative_end": 233, "source_hex": "6D EC 79 EC 2C 8E EC E8 EF E6 58 8A E4", "japanese_text": "の能力を表示する。", "korean_text": "능력을 표시합니다."},
    ],
    76: [
        {"relative_start": 25, "relative_end": 28, "source_hex": "DB C8 DA", "japanese_text": "レベル", "korean_text": "레벨"},
        {"relative_start": 64, "relative_end": 93, "source_hex": "ED EE EC 63 8E 45 50 8A EC 79 EC 2C 00 00 00 3C 3D ED 89 6E ED 7E ED 59 6D EC 79 EC 2C", "japanese_text": "制限をうける能力　　　()内は本来の能力", "korean_text": "제한받는 능력 ( ) 안은 본래 능력"},
        {"relative_start": 94, "relative_end": 98, "source_hex": "EC 63 EC 64", "japanese_text": "限界", "korean_text": "한계"},
        {"relative_start": 115, "relative_end": 119, "source_hex": "EC 71 EC 72", "japanese_text": "回避", "korean_text": "회피"},
        {"relative_start": 155, "relative_end": 159, "source_hex": "EC 57 EC 58", "japanese_text": "命中", "korean_text": "명중"},
        {"relative_start": 195, "relative_end": 211, "source_hex": "52 6D EE A7 7E EC F7 8D 5A 66 43 43 66 58 4A 14", "japanese_text": "この組み合わせでいいですか?", "korean_text": "이 조합으로 하시겠습니까?"},
        {"relative_start": 215, "relative_end": 217, "source_hex": "6E 43", "japanese_text": "はい", "korean_text": "예"},
        {"relative_start": 218, "relative_end": 221, "source_hex": "43 43 47", "japanese_text": "いいえ", "korean_text": "아니요"},
        {"relative_start": 225, "relative_end": 229, "source_hex": "EC 6F EC 81", "japanese_text": "地形", "korean_text": "지형"},
        {"relative_start": 233, "relative_end": 235, "source_hex": "EC B6", "japanese_text": "空", "korean_text": "공"},
        {"relative_start": 236, "relative_end": 238, "source_hex": "EC F1", "japanese_text": "陸", "korean_text": "육"},
        {"relative_start": 239, "relative_end": 241, "source_hex": "EC 4F", "japanese_text": "海", "korean_text": "해"},
        {"relative_start": 242, "relative_end": 244, "source_hex": "F0 0D", "japanese_text": "宇", "korean_text": "우"},
    ],
    94: [
        {"relative_start": 20, "relative_end": 33, "source_hex": "68 6D EF A7 4A 88 ED 36 80 7D 58 4A 14", "japanese_text": "どの章から始めますか?", "korean_text": "시작할 장?"},
        {"relative_start": 37, "relative_end": 55, "source_hex": "CD A4 9C 6D EF A7 EE FF 00 ED 78 EC 68 00 83 54 56 43", "japanese_text": "マサキの章　難度　やさしい", "korean_text": "마사키 장 쉬움"},
        {"relative_start": 59, "relative_end": 76, "source_hex": "D9 D4 11 BC 6D EF A7 EE FF ED 78 EC 68 00 74 63 45", "japanese_text": "リュ-ネの章　難度 ふつう", "korean_text": "류네 장 보통"},
    ],
    95: [
        {"relative_start": 20, "relative_end": 33, "source_hex": "68 6D EF A7 4A 88 ED 36 80 7D 58 4A 14", "japanese_text": "どの章から始めますか?", "korean_text": "시작할 장?"},
        {"relative_start": 37, "relative_end": 57, "source_hex": "CD A4 9C 6D EF A7 EE FF 00 1E 28 28 8E ED 04 43 7D 58 4A 14", "japanese_text": "マサキの章 ISSを使いますか?", "korean_text": "마사키 장 ISS 쓸까요?"},
        {"relative_start": 61, "relative_end": 76, "source_hex": "D9 D4 11 BC 6D EF A7 EE FF ED 78 EC 68 00 74", "japanese_text": "リュ-ネの章 難度 ふ", "korean_text": "류네 장 중"},
        {"relative_start": 80, "relative_end": 82, "source_hex": "6E 43", "japanese_text": "はい", "korean_text": "예"},
        {"relative_start": 83, "relative_end": 86, "source_hex": "43 43 47", "japanese_text": "いいえ", "korean_text": "아뇨"},
        {"relative_start": 87, "relative_end": 92, "source_hex": "ED 35 5C 8B 14", "japanese_text": "何それ?", "korean_text": "뭐죠?"},
    ],
    96: [
        {"relative_start": 20, "relative_end": 33, "source_hex": "68 6D EF A7 4A 88 ED 36 80 7D 58 4A 14", "japanese_text": "どの章から始めますか?", "korean_text": "시작할 장?"},
        {"relative_start": 37, "relative_end": 55, "source_hex": "CD A4 9C 6D EF A7 EE FF 00 ED 78 EC 68 00 83 54 56 43", "japanese_text": "マサキの章　難度　やさしい", "korean_text": "마사키 장 쉬움"},
        {"relative_start": 59, "relative_end": 76, "source_hex": "D9 D4 11 BC 6D EF A7 EE FF ED 78 EC 68 00 74 63 45", "japanese_text": "リュ-ネの章　難度 ふつう", "korean_text": "류네 장 보통"},
        {"relative_start": 80, "relative_end": 99, "source_hex": "A6 D4 95 6D EF A7 EE FF 00 ED 78 EC 68 00 7F 59 4A 56 43", "japanese_text": "シュウの章　難度　むずかしい", "korean_text": "슈우 장 어려움"},
    ],
    97: [
        {"relative_start": 20, "relative_end": 33, "source_hex": "68 6D EF A7 4A 88 ED 36 80 7D 58 4A 14", "japanese_text": "どの章から始めますか?", "korean_text": "시작할 장?"},
        {"relative_start": 37, "relative_end": 57, "source_hex": "CD A4 9C 6D EF A7 EE FF 00 1E 28 28 8E ED 04 43 7D 58 4A 14", "japanese_text": "マサキの章 ISSを使いますか?", "korean_text": "마사키 장 ISS 쓸까요?"},
        {"relative_start": 61, "relative_end": 76, "source_hex": "D9 D4 11 BC 6D EF A7 EE FF ED 78 EC 68 00 74", "japanese_text": "リュ-ネの章 難度 ふ", "korean_text": "류네 장 중"},
        {"relative_start": 80, "relative_end": 95, "source_hex": "A6 D4 95 6D EF A7 EE FF 00 ED 78 EC 68 00 7F", "japanese_text": "シュウの章 難度 む", "korean_text": "슈우 장 상"},
        {"relative_start": 99, "relative_end": 101, "source_hex": "6E 43", "japanese_text": "はい", "korean_text": "예"},
        {"relative_start": 102, "relative_end": 105, "source_hex": "43 43 47", "japanese_text": "いいえ", "korean_text": "아뇨"},
        {"relative_start": 106, "relative_end": 111, "source_hex": "ED 35 5C 8B 14", "japanese_text": "何それ?", "korean_text": "뭐죠?"},
    ],
}
SHARED_UI_EXACT_ONLY_INDICES = frozenset({11, 39, 64, 76, 94, 95, 96, 97})


def _patch_shared_common_master_labels(
    executable: bytearray,
    overlay_path: Path,
    glyph_map: dict[str, int],
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    """Apply the five byte-identical fixed-stride common tables in SLPS."""

    document = load_json(overlay_path)
    rows = [
        row
        for row in overlay_records(document)
        if str(row.get("asset_id")) == "common_ui_master_labels"
    ]
    expected_indices = {0, 2, 23, 24, 25}
    if {_int(row["entry_index"], "entry_index") for row in rows} != expected_indices:
        raise ValueError("shared common-master overlay coverage changed")
    spans: list[tuple[int, int]] = []
    records: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda item: _int(item["entry_index"], "entry_index")):
        index = _int(row["entry_index"], "entry_index")
        field = SHARED_COMMON_MASTER_FIELD_BASE + index * 4
        target = field + s32(executable, field)
        source = bytes.fromhex(str(row["raw_hex"]))
        if executable[target:target + len(source)] != source:
            raise ValueError(f"SLPS common master [{index}] source bytes changed")
        rebuilt = rebuild_row_record(source, row, glyph_map)
        if len(rebuilt) != len(source):
            raise ValueError(f"SLPS common master [{index}] changed fixed byte stride")
        executable[target:target + len(rebuilt)] = rebuilt
        spans.append((target, target + len(rebuilt)))
        records.append(
            {
                "entry_index": index,
                "pointer_field": field,
                "target": target,
                "bytes": len(rebuilt),
                "sha256": sha256(rebuilt),
            }
        )
    return spans, {
        "asset_id": "shared_common_ui_master_labels",
        "patched_records": len(records),
        "records": records,
    }


def _prepare_shared_music_demo_pool(
    executable: bytearray,
    overlay_document: dict[str, Any],
    glyph_map: dict[str, int],
) -> tuple[bytes, list[tuple[int, int]], dict[str, Any]]:
    """Rebuild SLPS's byte-identical music/demo title pool and its pointers."""

    inventory_path = (
        Path(__file__).resolve().parents[1]
        / "research"
        / "second_exe_ui_full_inventory.json"
    )
    inventory = load_json(inventory_path)
    group = inventory["common_music_demo_pool"]
    asset_id = str(group["asset_id"])
    source_base = _int(group["pool_start"], "pool_start")
    translations = _translation_map([overlay_document])
    rebuilt = bytearray()
    relative_by_index: dict[int, int] = {}
    source_target_by_index: dict[int, int] = {}
    nested_to_record: dict[int, int] = {}
    translated = 0
    for source_row in group["records"]:
        index = _int(source_row["sequential_index"], "sequential_index")
        source_target = SHARED_MUSIC_SOURCE_START + (
            _int(source_row["source_offset"], "source_offset") - source_base
        )
        source = bytes.fromhex(str(source_row["raw_hex"]))
        if executable[source_target:source_target + len(source)] != source:
            raise ValueError(f"SLPS music/demo source [{index}] changed")
        overlay = translations.get((asset_id, index))
        if source_row.get("translation_target") and overlay is None:
            raise ValueError(f"missing SLPS music/demo translation [{index}]")
        output = (
            _rebuild_music_demo_title(source, overlay, glyph_map, index)
            if overlay is not None else source
        )
        relative_by_index[index] = len(rebuilt)
        source_target_by_index[index] = source_target
        rebuilt.extend(output)
        translated += int(overlay is not None)
        for nested_index in source_row.get("nested_indices", []):
            nested_to_record[_int(nested_index, "nested_index")] = index

    if set(nested_to_record) != set(range(SHARED_MUSIC_POINTER_COUNT)):
        raise ValueError("SLPS music/demo nested-index coverage changed")
    pointer_updates: list[tuple[int, int]] = []
    for nested_index in range(SHARED_MUSIC_POINTER_COUNT):
        index = nested_to_record[nested_index]
        field = SHARED_MUSIC_POINTER_TABLE + nested_index * 4
        expected = source_target_by_index[index]
        if field + s32(executable, field) != expected:
            raise ValueError(
                f"SLPS music/demo pointer {nested_index} no longer targets 0x{expected:X}"
            )
        pointer_updates.append((field, relative_by_index[index]))
    for field, index in SHARED_MUSIC_HIDDEN_POINTERS.items():
        expected = source_target_by_index[index]
        if field + s32(executable, field) != expected:
            raise ValueError(f"SLPS hidden music pointer 0x{field:X} changed")
        pointer_updates.append((field, relative_by_index[index]))
    return bytes(rebuilt), pointer_updates, {
        "asset_id": "shared_common_music_demo_title_pool",
        "source_start": SHARED_MUSIC_SOURCE_START,
        "source_bytes": _int(group["pool_bytes"], "pool_bytes"),
        "record_count": len(group["records"]),
        "translated_records": translated,
        "stateful_layout_preserved_records": translated,
        "compacted_display_records": sum(
            index in MUSIC_DEMO_DISPLAY_COMPACTION_BY_INDEX
            for index in range(len(group["records"]))
        ),
        "pointer_count": len(pointer_updates),
        "rebuilt_bytes": len(rebuilt),
        "source_preserved_for_untracked_references": True,
    }


def _patch_shared_preview_conditions(
    executable: bytearray,
    overlay_document: dict[str, Any],
    glyph_map: dict[str, int],
) -> tuple[list[tuple[int, int]], dict[str, Any]]:
    """Patch only the two equal-size condition labels in SLPS's root pool."""

    inventory_path = (
        Path(__file__).resolve().parents[1]
        / "research"
        / "second_exe_ui_full_inventory.json"
    )
    inventory = load_json(inventory_path)
    group = inventory["common_preview_pool"]
    asset_id = str(group["asset_id"])
    source_base = _int(group["pool_start"], "pool_start")
    translations = _translation_map([overlay_document])
    spans: list[tuple[int, int]] = []
    records: list[dict[str, Any]] = []
    for source_row in group["records"]:
        index = _int(source_row["sequential_index"], "sequential_index")
        if index not in {0, 2}:
            continue
        target = SHARED_PREVIEW_SOURCE_START + (
            _int(source_row["source_offset"], "source_offset") - source_base
        )
        source = bytes.fromhex(str(source_row["raw_hex"]))
        if executable[target:target + len(source)] != source:
            raise ValueError(f"SLPS condition source [{index}] changed")
        overlay = translations.get((asset_id, index))
        if overlay is None:
            raise ValueError(f"missing SLPS condition translation [{index}]")
        overlay = dict(overlay)
        source_text = str(source_row.get("japanese_text", ""))
        overlay["korean_text"] = UI_DISPLAY_COMPACTION[source_text]
        rebuilt = rebuild_row_record(source, overlay, glyph_map)
        if len(rebuilt) != len(source):
            raise ValueError(f"SLPS condition [{index}] changed byte length")
        executable[target:target + len(rebuilt)] = rebuilt
        spans.append((target, target + len(rebuilt)))
        records.append(
            {"sequential_index": index, "target": target, "bytes": len(rebuilt)}
        )
    return spans, {
        "asset_id": "shared_preview_conditions",
        "patched_records": len(records),
        "records": records,
        "long_preview_records_deferred": len(group["records"]) - len(records),
    }


def _shared_font_donor_spans(executable: bytes | bytearray) -> list[tuple[int, int]]:
    start = SHARED_FONT_BASE + SHARED_FONT_DONOR_GLYPH_START * SHARED_GLYPH_BYTES
    end = SHARED_FONT_BASE + SHARED_FONT_DONOR_GLYPH_END * SHARED_GLYPH_BYTES
    if end > len(executable):
        raise ValueError("SLPS shared font donor lies outside the executable")
    if sha256(bytes(executable[start:end])) != SHARED_FONT_DONOR_SHA256:
        raise ValueError("SLPS shared font donor tail changed")
    spans: list[tuple[int, int]] = []
    cursor = start
    for glyph in SHARED_FONT_DONOR_EXCLUDED_GLYPHS:
        if not SHARED_FONT_DONOR_GLYPH_START <= glyph < SHARED_FONT_DONOR_GLYPH_END:
            raise AssertionError("invalid shared font donor exclusion")
        glyph_start = SHARED_FONT_BASE + glyph * SHARED_GLYPH_BYTES
        if cursor < glyph_start:
            spans.append((cursor, glyph_start))
        cursor = glyph_start + SHARED_GLYPH_BYTES
    if cursor < end:
        spans.append((cursor, end))
    return spans


def patch_shared_executable_ui(
    executable_path: Path,
    ui_overlay_path: Path,
    glyph_map: dict[str, int],
) -> dict[str, Any]:
    """Patch SLPS_020.70's shared UI master with guarded pointer repacking.

    The reviewed overlay is authored against SECOND.WAR, while the shared
    executable has byte-identical source runs for the applicable entries.  A
    source mismatch is recorded as deferred rather than guessed; this keeps
    the front-end boot path fail-closed while still translating every common
    save/load/option/menu record that is proven identical.
    """

    source_executable = executable_path.read_bytes()
    executable = bytearray(source_executable)
    if len(executable) != 0xBA800 or executable[:8] != PSX_EXE_MAGIC:
        raise ValueError("unexpected SLPS_020.70 executable shape")
    document = load_json(ui_overlay_path)
    music_raw, music_pointer_updates, music_manifest = (
        _prepare_shared_music_demo_pool(executable, document, glyph_map)
    )
    rows = [
        row
        for row in overlay_records(document)
        if str(row.get("asset_id")) == "second_ui_script_master"
    ]
    rows_by_index = {
        _int(row["entry_index"], "entry_index"): row
        for row in rows
    }

    def partial_rebuild(
        raw: bytes,
        replacements: list[dict[str, Any]],
        *,
        preserve_display_bytes: bool = False,
    ) -> tuple[bytes, int, list[str]]:
        """Apply only source spans proven in this executable's VM record.

        A few shared records have harmless regional differences (for example
        SLPS labels ``終了`` where SECOND has ``セ-ブ``).  Rejecting the whole
        record would leave an otherwise translatable settings page Japanese;
        this helper preserves controls and applies every matching glyph run,
        reporting the few deferred spans for later review.
        """

        _end, tokens = _parse_record(raw, 0, len(raw), SECOND_UI_VM_GRAMMAR)
        boundaries = {0, *(token.start for token in tokens), *(token.end for token in tokens)}
        controls = control_signature(raw, grammar=SECOND_UI_VM_GRAMMAR)
        ordered = sorted(
            replacements,
            key=lambda item: _int(item["relative_start"], "relative_start"),
        )
        output = bytearray()
        cursor = 0
        applied = 0
        skipped: list[str] = []
        for replacement in ordered:
            start = _int(replacement["relative_start"], "relative_start")
            end = _int(
                replacement.get("relative_end", replacement.get("relative_end_exclusive")),
                "relative_end",
            )
            source = raw[start:end] if 0 <= start <= end <= len(raw) else b""
            expected = replacement.get("source_hex")
            valid = (
                start >= cursor
                and end > start
                and end <= len(raw) - 1
                and start in boundaries
                and end in boundaries
                and source
                and (expected is None or source == bytes.fromhex(str(expected)))
                and not any(
                    token.start < end
                    and token.end > start
                    and token.kind != "glyph"
                    for token in tokens
                )
            )
            if not valid:
                skipped.append(str(replacement.get("japanese_text", "unknown")))
                continue
            display_text = str(replacement.get("display_text")) if isinstance(
                replacement.get("display_text"), str
            ) else UI_DISPLAY_COMPACTION.get(
                str(replacement.get("japanese_text", "")),
                str(replacement["korean_text"]),
            )
            capacity = sum(
                1 for token in tokens if start <= token.start and token.end <= end
            )
            span_tokens = [
                token
                for token in tokens
                if start <= token.start and token.end <= end
            ]
            next_token = next(
                (token for token in tokens if token.start >= end),
                None,
            )
            try:
                encoded = _encode_fixed_span_text(
                    display_text,
                    glyph_map,
                    capacity,
                    preserve_width=True,
                    byte_capacity=(
                        _int(replacement["output_byte_capacity"], "output_byte_capacity")
                        if preserve_display_bytes and replacement.get("output_byte_capacity") is not None
                        else (end - start) if preserve_display_bytes else None
                    ),
                    renderer_layout=(
                        _renderer_layout_signature(span_tokens)
                        if not preserve_display_bytes else None
                    ),
                    require_matching_phase=_next_token_requires_matching_phase(
                        next_token
                    ),
                )
            except ValueError:
                skipped.append(str(replacement.get("japanese_text", "unknown")))
                continue
            output.extend(raw[cursor:start])
            output.extend(encoded)
            cursor = end
            applied += 1
        output.extend(raw[cursor:])
        rebuilt = bytes(output)
        if control_signature(rebuilt, grammar=SECOND_UI_VM_GRAMMAR) != controls:
            raise ValueError("partial shared UI rebuild changed renderer controls")
        return rebuilt, applied, skipped

    pointer_rows: list[dict[str, Any]] = []
    unique_targets: dict[int, dict[str, Any]] = {}
    for index in range(107):
        field = SHARED_UI_MASTER_HEADER + 4 + index * 4
        target = field + s32(executable, field)
        if not 0 <= target < SHARED_UI_MASTER_BOUND:
            raise ValueError(f"SLPS UI pointer {index} targets outside master")
        end, _tokens = parse_second_ui_vm_record(
            executable, target, len(executable)
        )
        raw = bytes(executable[target:end])
        row = (
            None
            if index in SHARED_UI_EXACT_ONLY_INDICES
            else rows_by_index.get(index)
        )
        exact_replacements = SHARED_UI_EXACT_REPLACEMENTS.get(index, [])
        if exact_replacements:
            row = dict(row or {
                "asset_id": "second_ui_script_master",
                "entry_index": index,
                "translation_target": True,
            })
            row["replacements"] = (
                list(row.get("replacements") or [])
                + [dict(item) for item in exact_replacements]
            )
        plan = {
            "entry_index": index,
            "pointer_field": field,
            "source_offset": target,
            "raw": raw,
            "output": raw,
            "translated": False,
            "deferred": None,
        }
        if row is not None:
            # These overlays are authored for SECOND.WAR.  Shared SLPS text
            # spans can be reused when their guarded bytes match, but its
            # dynamic-value producers retain separate retail strides and must
            # not receive SECOND-only F8/FC argument compensation.
            row = dict(row)
            row.pop("control_patches", None)
            inferred = _infer_ui_text_replacements(
                raw,
                list(row.get("replacements") or []),
            )
            if inferred:
                row = dict(row)
                row["replacements"] = list(row.get("replacements") or []) + inferred
            try:
                plan["output"] = rebuild_row_record(
                    raw,
                    row,
                    glyph_map,
                    grammar=SECOND_UI_VM_GRAMMAR,
                )
                plan["translated"] = True
            except ValueError as exc:
                replacements = list(row.get("replacements") or [])
                # SLPS uses 終了 in this slot; keep the shared executable's
                # wording while translating it to the same-width Korean
                # command used by the front-end.
                if index == 11:
                    replacements.append(
                        {
                            "relative_start": 67,
                            "relative_end": 71,
                            "korean_text": "종료",
                        }
                    )
                    replacements.extend(
                        [
                            {"relative_start": 84, "relative_end": 89, "korean_text": "턴 수"},
                            {"relative_start": 90, "relative_end": 94, "korean_text": "자금"},
                        ]
                    )
                elif index == 39:
                    replacements.extend(
                        [
                            {"relative_start": 95, "relative_end": 112, "korean_text": "+셀+스타리셋"},
                            {"relative_start": 113, "relative_end": 120, "korean_text": "버튼설정"},
                            {"relative_start": 124, "relative_end": 128, "korean_text": "결정"},
                            {"relative_start": 129, "relative_end": 134, "korean_text": "취소"},
                            {"relative_start": 135, "relative_end": 142, "korean_text": "속도업"},
                            {"relative_start": 143, "relative_end": 150, "korean_text": "전체맵"},
                            {"relative_start": 151, "relative_end": 157, "korean_text": "부대"},
                            {"relative_start": 158, "relative_end": 169, "korean_text": "아군다음"},
                            {"relative_start": 170, "relative_end": 181, "korean_text": "아군이전"},
                            {"relative_start": 182, "relative_end": 193, "korean_text": "적군다음"},
                            {"relative_start": 194, "relative_end": 205, "korean_text": "적군이전"},
                            {"relative_start": 206, "relative_end": 218, "korean_text": "초기화"},
                        ]
                    )
                elif index == 64:
                    replacements.extend(
                        [
                            {"relative_start": 136, "relative_end": 150, "korean_text": "유닛 이동력 +2."},
                            {"relative_start": 151, "relative_end": 164, "korean_text": "유닛 HP +1500."},
                            {"relative_start": 165, "relative_end": 177, "korean_text": "유닛 EN +100."},
                            {"relative_start": 178, "relative_end": 193, "korean_text": "유닛 운동성 +50."},
                            {"relative_start": 194, "relative_end": 208, "korean_text": "유닛 장갑 +600."},
                        ]
                    )
                elif index == 76:
                    replacements.extend(
                        [
                            {"relative_start": 64, "relative_end": 93, "korean_text": "제한받는 능력 ( ) 안은 본래 능력"},
                            {"relative_start": 94, "relative_end": 98, "korean_text": "한계"},
                            {"relative_start": 115, "relative_end": 119, "korean_text": "회피"},
                            {"relative_start": 155, "relative_end": 159, "korean_text": "명중"},
                            {"relative_start": 195, "relative_end": 211, "korean_text": "이 조합으로 하시겠습니까?"},
                            {"relative_start": 215, "relative_end": 217, "korean_text": "예"},
                            {"relative_start": 218, "relative_end": 221, "korean_text": "아니요"},
                            {"relative_start": 225, "relative_end": 229, "korean_text": "지형"},
                            {"relative_start": 233, "relative_end": 235, "korean_text": "공중"},
                            {"relative_start": 236, "relative_end": 238, "korean_text": "육지"},
                            {"relative_start": 239, "relative_end": 241, "korean_text": "바다"},
                            {"relative_start": 242, "relative_end": 244, "korean_text": "우주"},
                        ]
                    )
                try:
                    rebuilt, applied, skipped = partial_rebuild(
                        raw,
                        replacements,
                        preserve_display_bytes=bool(row.get("preserve_display_bytes")),
                    )
                except ValueError:
                    rebuilt, applied, skipped = raw, 0, []
                if applied:
                    plan["output"] = rebuilt
                    plan["translated"] = True
                    plan["deferred"] = {
                        "original_error": str(exc),
                        "unmatched_spans": skipped,
                    }
                else:
                    plan["deferred"] = str(exc)
        else:
            inferred = _infer_ui_text_replacements(raw, [])
            if inferred:
                plan["output"], _applied, _skipped = partial_rebuild(raw, inferred)
                plan["translated"] = True
        pointer_rows.append(plan)
        unique_targets.setdefault(target, plan)

    source_spans = [
        (int(plan["source_offset"]), int(plan["source_offset"]) + len(plan["raw"]))
        for plan in unique_targets.values()
    ]
    donor_spans = _shared_font_donor_spans(executable)
    pool = StaticPool([*source_spans, *donor_spans])
    music_start = pool.add(
        music_raw,
        asset_id="shared_common_music_demo_title_pool",
        key="complete_ordered_pool",
        intern=False,
        alignment=4,
    )
    music_manifest["pool_start"] = music_start
    music_manifest["pool_end"] = music_start + len(music_raw)
    target_by_old: dict[int, int] = {}
    # Allocate largest records first so the fragmented donor tail cannot leave
    # a small unusable hole for a long menu page.
    for old_target, plan in sorted(
        unique_targets.items(), key=lambda item: (-len(bytes(item[1]["output"])), item[0])
    ):
        target_by_old[old_target] = pool.add(
            bytes(plan["output"]),
            asset_id="shared_ui_master",
            key=f"record[{plan['entry_index']}]",
            intern=False,
        )
    pool.commit(executable)
    for field, relative_target in music_pointer_updates:
        patch_relative_pointer(executable, field, music_start + relative_target)
    for plan in pointer_rows:
        field = int(plan["pointer_field"])
        target = target_by_old[int(plan["source_offset"])]
        patch_relative_pointer(executable, field, target)

    for plan in pointer_rows:
        field = int(plan["pointer_field"])
        target = field + s32(executable, field)
        expected = bytes(plan["output"])
        if executable[target:target + len(expected)] != expected:
            raise AssertionError(
                f"SLPS shared UI pointer verification failed for {plan['entry_index']}"
            )
        end, _tokens = parse_second_ui_vm_record(
            executable, target, len(executable)
        )
        if end != target + len(expected):
            raise AssertionError("SLPS shared UI record boundary changed")
    for field, relative_target in music_pointer_updates:
        if field + s32(executable, field) != music_start + relative_target:
            raise AssertionError("SLPS music/demo pointer verification failed")

    save_prompt_spans, save_prompt_manifest = _patch_common_save_prompt_records(
        executable, glyph_map
    )
    common_master_path = ui_overlay_path.with_name(
        "second_ui_common_master_overlay.json"
    )
    common_master_spans, common_master_manifest = (
        _patch_shared_common_master_labels(
            executable, common_master_path, glyph_map
        )
    )
    preview_overlay_path = ui_overlay_path.with_name(
        "second_ui_preview_overlay.json"
    )
    preview_condition_spans, preview_condition_manifest = (
        _patch_shared_preview_conditions(
            executable, load_json(preview_overlay_path), glyph_map
        )
    )

    pointer_fields = [int(plan["pointer_field"]) for plan in pointer_rows]
    pointer_fields.extend(field for field, _relative in music_pointer_updates)
    change_audit = _audit_allowed_changes(
        source_executable,
        executable,
        [
            *((offset, offset + len(bytes(raw))) for offset, raw in pool.payloads.items()),
            *((field, field + 4) for field in pointer_fields),
            *save_prompt_spans,
            *common_master_spans,
            *preview_condition_spans,
        ],
    )
    executable_path.write_bytes(executable)
    return {
        "format": "srwcb-shared-executable-ui-static-repack-v1",
        "path": str(executable_path).replace("\\", "/"),
        "entry_count": len(pointer_rows),
        "translated_records": sum(bool(plan["translated"]) for plan in pointer_rows),
        "deferred_records": [
            {
                "entry_index": plan["entry_index"],
                "reason": plan["deferred"],
            }
            for plan in pointer_rows
            if plan["deferred"]
        ],
        "source_span_capacity": pool.capacity,
        "allocated_payload_bytes": pool.used,
        "remaining_source_span_bytes": pool.capacity - pool.used,
        "static_font_donor": {
            "file_start": SHARED_FONT_BASE
            + SHARED_FONT_DONOR_GLYPH_START * SHARED_GLYPH_BYTES,
            "file_end": SHARED_FONT_BASE + SHARED_FONT_DONOR_GLYPH_END * SHARED_GLYPH_BYTES,
            "glyph_start": SHARED_FONT_DONOR_GLYPH_START,
            "glyph_end_exclusive": SHARED_FONT_DONOR_GLYPH_END,
            "excluded_glyphs": list(SHARED_FONT_DONOR_EXCLUDED_GLYPHS),
            "source_sha256": SHARED_FONT_DONOR_SHA256,
        },
        "change_audit": change_audit,
        "save_prompt_records": save_prompt_manifest,
        "common_master_labels": common_master_manifest,
        "music_demo_titles": music_manifest,
        "preview_conditions": preview_condition_manifest,
        "executable_sha256": sha256(bytes(executable)),
    }


__all__ = [
    "collect_korean_ui_texts",
    "patch_second_executable_ui",
    "patch_shared_executable_ui",
    "encode_ui_text",
    "parse_renderer_record",
    "parse_second_ui_vm_record",
    "control_signature",
    "ROOT_RELOCATION_CAVE_START",
    "ROOT_RELOCATION_CAVE_END",
]
