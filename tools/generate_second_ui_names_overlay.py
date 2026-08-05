#!/usr/bin/env python3
"""Extract and translate SECOND.WAR pilot/unit name tables.

The output is data-only: this tool never modifies the executable.  Approved
glossary spellings and consistent speaker labels mined from the reviewed
dialogue overlay take precedence over the local canonical-name catalogue.
Anything that reaches the mechanical kana transliterator is explicitly
marked for later review.
"""

from __future__ import annotations

# --- 이식용 부트스트랩 (자동 삽입) ---
import os as _os, sys as _sys
_d = _os.path.dirname(_os.path.abspath(__file__))
while _d != _os.path.dirname(_d) and not _os.path.exists(_os.path.join(_d, "srwcb_paths.py")):
    _d = _os.path.dirname(_d)
if _d not in _sys.path:
    _sys.path.insert(0, _d)
import srwcb_paths as _P
_P.ensure_dirs()
for _sub in ("tools", "third-ui", "ex-ui", "tr-ui", "audit", "menu-align", "second-fixes"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------

import hashlib
import json
import re
import struct
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = _P.WORK
EXECUTABLE = _P.EXTRACTED / "SECOND" / "SECOND.WAR"
GLYPH_MAP = _P.FONT_MAPPING
GLOSSARY = _P.LEDGER / "glossary_candidates.json"
LEDGER = _P.LEDGER / "second_translation_ledger.json"
DIALOGUE_OVERLAY = _P.TRANSLATION / "second_translation_overlay.json"
OUTPUT = _P.TRANSLATION / "second_ui_names_overlay.json"

PSX_EXE_FILE_TO_RAM_BIAS = 0x8000F800
TABLES = (
    {
        "id": "short_pilot_names",
        "kind": "pilot_short_name",
        "header_offset": 0x10CE0C,
        "pointer_table_offset": 0x10CE10,
        "count": 400,
        "pointer_table_bytes": 0x640,
    },
    {
        "id": "full_pilot_names",
        "kind": "pilot_full_name",
        "header_offset": 0x10DD64,
        "pointer_table_offset": 0x10DD68,
        "count": 400,
        "pointer_table_bytes": 0x640,
    },
    {
        "id": "unit_names",
        "kind": "unit_name",
        "header_offset": 0x10F478,
        "pointer_table_offset": 0x10F47C,
        "count": 448,
        "pointer_table_bytes": 0x700,
    },
)

JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def u32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def s32(data: bytes, offset: int) -> int:
    return struct.unpack_from("<i", data, offset)[0]


def hex_offset(value: int) -> str:
    return f"0x{value:X}"


def load_reviewed_glyphs() -> tuple[dict[int, str | None], dict[int, dict[str, Any]]]:
    document = json.loads(GLYPH_MAP.read_text(encoding="utf-8"))
    rows = {int(row["glyph_index"]): row for row in document["rows"]}
    if set(rows) != set(range(0xB00)):
        raise ValueError("reviewed glyph map is not the complete 0xB00-glyph map")
    # The legacy ``character`` column was serialised through a Windows code
    # page in an early mapping pass.  ``unicode`` is the reviewed authority
    # used by the production dialogue codec.
    glyphs: dict[int, str | None] = {}
    for index, row in rows.items():
        unicode_label = str(row.get("unicode", ""))
        if unicode_label.startswith("U+") and " " not in unicode_label:
            glyphs[index] = chr(int(unicode_label[2:], 16))
        else:
            glyphs[index] = None
    return glyphs, rows


def decode_record(
    executable: bytes,
    start: int,
    glyphs: dict[int, str | None],
    glyph_rows: dict[int, dict[str, Any]],
) -> tuple[str, bytes, list[dict[str, Any]]]:
    position = start
    output: list[str] = []
    unresolved: list[dict[str, Any]] = []
    while True:
        if position >= len(executable):
            raise ValueError(f"unterminated name at {start:#x}")
        first = executable[position]
        position += 1
        if first == 0xFF:
            break
        if first < 0xEB:
            glyph_index = first
        elif first <= 0xF5:
            if position >= len(executable):
                raise ValueError(f"truncated multibyte glyph at {position - 1:#x}")
            glyph_index = ((first - 0xEB) << 8) | executable[position]
            position += 1
        else:
            raise ValueError(
                f"unexpected control 0x{first:02X} in name at {position - 1:#x}"
            )
        character = glyphs[glyph_index]
        if character is None:
            token = f"<G:0x{glyph_index:03X}>"
            output.append(token)
            row = glyph_rows[glyph_index]
            unresolved.append(
                {
                    "glyph_index": glyph_index,
                    "glyph_index_hex": f"0x{glyph_index:03X}",
                    "message_bytes": row["message_bytes"],
                    "mapping_confidence": row.get("confidence"),
                    "token": token,
                }
            )
        else:
            output.append(character)
    return "".join(output), executable[start:position], unresolved


def load_approved_glossary() -> tuple[dict[str, str], dict[str, Any]]:
    document = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    approved: dict[str, str] = {}
    for section in ("speaker_names", "katakana_terms", "kanji_compounds"):
        for row in document[section]:
            korean = row.get("ko_approved")
            if row.get("status") != "approved" or not korean:
                continue
            japanese = row["ja"]
            previous = approved.get(japanese)
            if previous is not None and previous != korean:
                raise ValueError(
                    f"approved glossary conflict for {japanese!r}: {previous!r} / {korean!r}"
                )
            approved[japanese] = korean
    return approved, document


def load_overlay_speaker_labels() -> dict[str, str]:
    """Mine only unanimous translated speaker prefixes from the dialogue overlay."""

    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    overlay = json.loads(DIALOGUE_OVERLAY.read_text(encoding="utf-8"))["translations"]
    candidates: dict[str, Counter[str]] = defaultdict(Counter)
    for occurrence in ledger["occurrences"]:
        translation = overlay.get(occurrence["translation_memory_key"])
        if not translation:
            continue
        for mention in occurrence["japanese"].get("speaker_mentions", []):
            if mention.get("start") != 0:
                continue
            korean_part = translation.get("ko_parts", {}).get(mention["part_id"])
            if not isinstance(korean_part, str) or "「" not in korean_part:
                continue
            korean_label = korean_part.split("「", 1)[0].strip()
            if not korean_label or JAPANESE_RE.search(korean_label):
                continue
            candidates[mention["ja"]][korean_label] += 1

    unanimous: dict[str, str] = {}
    for japanese, values in candidates.items():
        if len(values) == 1:
            unanimous[japanese] = next(iter(values))
    return unanimous


# Full pilot names are stored with '=' as the in-game word separator.  The
# catalogue mirrors that structure with '|'; this lets the generator derive
# exactly matching short labels from the same components.
PILOT_FULL_COMPONENTS: dict[str, str] = {
    "マチルダ=アジャン": "마틸다|아잔",
    "ランバ=ラル": "람바|랄",
    "クラウレ=ハモン": "크라우레|하몬",
    "シャリア=ブル": "샤리아|불",
    "スレッガ-=ロウ": "슬레거|로우",
    "アムロ=レイ": "아무로|레이",
    "ブライト=ノア": "브라이트|노아",
    "クェス=パラヤ": "퀘스|파라야",
    "チェ-ン=アギ": "첸|아기",
    "ケ-ラ=スゥ": "케라|수",
    "ハサウェイ=ノア": "하사웨이|노아",
    "ギュネイ=ガス": "규네이|거스",
    "ナナイ=ミゲル": "나나이|미겔",
    "レズン=シュナイダ-": "레즌|슈나이더",
    "アストナ-ジ=メドッソ": "아스토나지|메돗소",
    "カミ-ユ=ビダン": "카미유|비단",
    "クワトロ=バジ-ナ": "크와트로|바지나",
    "エマ=シ-ン": "에마|신",
    "ファ=ユイリィ": "화|유이리",
    "カツ=コバヤシ": "카츠|코바야시",
    "フォウ=ムラサメ": "포우|무라사메",
    "ベルト-チカ=イルマ": "벨토치카|이르마",
    "ヘンケン=ベッケナ-": "헨켄|베케너",
    "カクリコン=カク-ラ-": "카크리콘|카쿠라",
    "サラ=ザビアロフ": "사라|자비아로프",
    "ゲ-ツ=キャパ": "게이츠|캐퍼",
    "ジェリド=メサ": "제리드|메사",
    "ジャマイカン=ダニンガン": "자마이칸|다닝건",
    "ハマ-ン=カ-ン": "하만|칸",
    "ベン=ウッダ-": "벤|우더",
    "マウア-=ファラオ": "마우아|파라오",
    "ライラ=ミラ=ライラ": "라일라|미라|라이라",
    "ヤザン=ゲ-ブル": "야잔|게이블",
    "ダンゲル=ク-パ-": "단겔|쿠퍼",
    "ラムサス=ハサ": "람사스|하사",
    "パプティマス=シロッコ": "팝티머스|시로코",
    "バスク=オム": "바스크|옴",
    "ガディ=キンゼ-": "가디|킨제",
    "ブラン=ブルタ-ク": "브란|블루타크",
    "ブレックス=フォ-ラ": "브렉스|포러",
    "ジャミトフ=ハイマン": "자미토프|하이만",
    "ジュド-=ア-シタ": "쥬도|아시타",
    "ハヤト=コバヤシ": "하야토|코바야시",
    "ル-=ルカ": "루|루카",
    "エルピ-=プル": "엘피|플",
    "リィナ=ア-シタ": "리나|아시타",
    "ビ-チャ=オ-レグ": "비챠|올레그",
    "イ-ノ=アッバ-ブ": "이노|압바브",
    "モンド=アガケ": "몬도|아가케",
    "エル=ビアンノ": "엘|비안노",
    "マシュマ-=セロ": "마슈마|세로",
    "イリア=パゾム": "일리아|파좀",
    "キャラ=ス-ン": "캐라|슨",
    "ゴットン=ゴ-": "곳톤|고",
    "ラカン=ダカラン": "라칸|다카란",
    "アリアス=モマ": "아리아스|모마",
    "エマリ-=オンス": "에마리|온스",
    "ミネバ=ザビ": "미네바|자비",
    "シ-ブック=アノ-": "시북|아노",
    "セシリ-=フェアチャイルド": "세실리|페어차일드",
    "ビルギット=ピリヨ": "비르기트|피리요",
    "クリスチ-ナ=マッケンジ-": "크리스티나|매켄지",
    "バ-ナ-ド=ワイズマン": "버나드|와이즈먼",
    "コウ=ウラキ": "코우|우라키",
    "チャック=キ-ス": "척|키스",
    "アナベル=ガト-": "애너벨|가토",
    "ケリィ=レズナ-": "켈리|레즈너",
    "シ-マ=ガラハウ": "시마|가라하우",
    "ニナ=パ-プルトン": "니나|퍼플턴",
    "モ-ラ=バシット": "모라|바시트",
    "ジョン=コ-ウェン": "존|코웬",
    "エギ-ユ=デラ-ズ": "에규|데라즈",
    "ジャック=キング": "잭|킹",
    "メリ-=キング": "메리|킹",
    "ビュ-ティフル=タチバナ": "뷰티풀|타치바나",
    "シャア=アズナブル": "샤아|아즈나블",
    "フラウ=ボウ": "프라우|보우",
    "カイ=シデン": "카이|시덴",
    "カムラン=ブル-ム": "캄란|블룸",
    "ミライ=ヤシマ": "미라이|야시마",
    "ララァ=スン": "라라아|슨",
    "セイラ=マス": "세이라|마스",
    "フラナガン=ブ-ン": "프라나간|분",
    "ガルマ=ザビ": "가르마|자비",
    "リュウ=ホセイ": "류|호세이",
    "レコア=ロンド": "레코아|론도",
    "グレミ-=トト": "그레미|토토",
    "ニ-=ギ-レン": "니|기렌",
    "オウギュスト=ギダン": "오귀스트|기단",
    "ランス=ギ-レン": "란스|기렌",
    "アルフレッド=イズルハ": "알프레드|이즈루하",
    "シュタイナ-=ハ-ディ-": "슈타이너|하디",
    "カロッゾ=ロナ": "카롯조|로나",
    "アンナマリ-=ブル-ジュ": "안나마리|브루지",
    "ドレル=ロナ": "도렐|로나",
    "ザビ-ネ=シャル": "자비네|샤르",
    "アルファ=A=ベイト": "알파|A|베이트",
    "ノイエン=ビッタ-": "노이엔|비터",
    "エイパ-=シナプス": "에이퍼|시냅스",
    "ベルナルド=モンシア": "베르나르도|몬시아",
    "ルセット=オデビ-": "루셋|오데비",
    "キャプテン=ラドラ": "캡틴|라드라",
    "デュ-ク=フリ-ド": "듀크|프리드",
    "マリア=フリ-ド": "마리아|프리드",
    "マサキ=アンド-": "마사키|안도",
    "リュ-ネ=ゾルダ-ク": "류네|졸다크",
    "シュウ=シラカワ": "슈우|시라카와",
    "イセリナ=エッシェンバッハ": "이세리나|에셴바흐",
    "カミ-ユ=ビダン": "카미유|비단",
    "ベラ=ロナ": "베라|로나",
    "ビアン=ゾルダ-ク": "비안|졸다크",
    "ビアン=ゾルダ-グ": "비안|졸다크",
    "フォウ=ムラサメ": "포우|무라사메",
    "ロザミア=バダム": "로자미아|바담",
    "ギレン=ザビ": "기렌|자비",
    "キシリア=ザビ": "키시리아|자비",
    "ドズル=ザビ": "도즐|자비",
    "偽デュ-ク=フリ-ド": "가짜 듀크|프리드",
    "ミリィ=チルダ-": "밀리|칠더",
}


PILOT_DIRECT: dict[str, str] = {
    "連邦軍兵士": "연방군 병사",
    "兜甲児": "카부토 코우지",
    "弓さやか": "유미 사야카",
    "あしゅら男爵": "아수라 남작",
    "ブロッケン伯爵": "브로켄 백작",
    "Drヘル": "닥터 헬",
    "弓教授": "유미 교수",
    "剣鉄也": "츠루기 테츠야",
    "炎ジュン": "호노오 쥰",
    "暗黒大将軍": "암흑대장군",
    "流竜馬": "나가레 료마",
    "神隼人": "진 하야토",
    "車弁慶": "쿠루마 벤케이",
    "早乙女ミチル": "사오토메 미치루",
    "早乙女博士": "사오토메 박사",
    "葵豹馬": "아오이 효마",
    "浪花十三": "나니와 쥬조",
    "西川大作": "니시카와 다이사쿠",
    "南原ちずる": "난바라 치즈루",
    "北小介": "키타 코스케",
    "四谷博士": "요츠야 박사",
    "破嵐万丈": "하란 반죠",
    "ギャリソン時田": "개리슨 토키타",
    "三条レイカ": "산죠 레이카",
    "戸田突太": "토다 톳타",
    "レビル将軍": "레빌 장군",
    "胡蝶鬼": "호접귀",
    "巴武蔵": "토모에 무사시",
    "鉄甲鬼": "철갑귀",
    "ひびき洸": "히비키 아키라",
    "明日香麗": "아스카 레이",
    "<G:0x515>野マリ": "사쿠라노 마리",
    "神宮寺力": "진구지 리키",
    "猿丸太郎": "사루마루 타로",
    "牧葉ひかる": "마키바 히카루",
    "DC兵士": "DC 병사",
    "DCエリ-ト兵": "DC 정예병",
    "人工知能": "인공지능",
    "人工知能改": "개량 인공지능",
    "強化バイオロイド": "강화 바이오로이드",
    "バイオロイド兵士": "바이오로이드 병사",
    "DCスナイパ-": "DC 저격병",
    "DC狙撃兵": "DC 저격병",
    "DC強化兵": "DC 강화병",
    "戦闘獣ダンテ": "전투수 단테",
    "若い男": "젊은 남자",
    "若い女": "젊은 여자",
    "中年": "중년",
    "ハロ": "하로",
    "ト-レス": "토레스",
    "プルツ-": "플 투",
    "バ-ニィ": "버니",
    "ブロッケン": "브로켄",
    "ガル-ダ": "가루다",
    "ミ-ア": "미아",
    "オレアナ": "오레아나",
    "ロペット": "로페트",
    "ギャリソン": "개리슨",
    "ビュ-ティ": "뷰티",
    "アコ-ス": "아코스",
    "ジ-ン": "진",
    "ボラスキニフ": "보라스키니프",
    "デミトリ-": "데미트리",
    "ドレン": "도렌",
    "コンスコン": "콘스콘",
    "クランプ": "크람프",
    "ミハル": "미하루",
    "レビル": "레빌",
    "スレンダ-": "슬렌더",
    "トクワン": "토쿠완",
    "ワッケイン": "와케인",
    "アポリ-": "아폴리",
    "ロベルト": "로베르토",
    "サエグサ": "사에구사",
    "ロ-レライ": "로렐라이",
    "シャ-キン": "샤킨",
    "キリカ": "키리카",
    "ナイ-ダ": "나이다",
    "シロ": "시로",
    "クロ": "쿠로",
    "チカ": "치카",
    "メキボス": "메키보스",
    "カリウス": "카리우스",
    "ハサン": "하산",
    "アギ-ハ": "아기하",
    "シカログ": "시카로그",
    "ヴィガジ": "비가지",
    "ウェンドロ": "웬드로",
}


PILOT_SHORT_DIRECT: dict[str, str] = {
    "兵士": "병사",
    "甲児": "코우지",
    "さやか": "사야카",
    "弓教授": "유미 교수",
    "鉄也": "테츠야",
    "ジュン": "쥰",
    "暗黒大将軍": "암흑대장군",
    "リョウ": "료",
    "ハヤト": "하야토",
    "ベンケイ": "벤케이",
    "ミチル": "미치루",
    "豹馬": "효마",
    "十三": "쥬조",
    "大作": "다이사쿠",
    "ちずる": "치즈루",
    "小介": "코스케",
    "万丈": "반죠",
    "レイカ": "레이카",
    "トッポ": "톳타",
    "胡蝶鬼": "호접귀",
    "ムサシ": "무사시",
    "鉄甲鬼": "철갑귀",
    "洸": "아키라",
    "麗": "레이",
    "マリ": "마리",
    "神宮寺": "진구지",
    "猿丸": "사루마루",
    "ひかる": "히카루",
    "剣造": "켄조",
    "所員": "연구원",
    "DC兵士": "DC 병사",
    "DCエリ-ト兵": "DC 정예병",
    "DC狙撃兵": "DC 저격병",
    "DC強化兵": "DC 강화병",
    "強化バイオロイド": "강화 바이오로이드",
    "バイオロイド兵士": "바이오로이드 병사",
    "人工知能": "인공지능",
    "人工知能改": "개량 인공지능",
    "戦闘獣ダンテ": "전투수 단테",
    "若い男": "젊은 남자",
    "若い女": "젊은 여자",
    "中年": "중년",
}


UNIT_OVERRIDES: dict[str, str] = {
    "ガンダム": "건담",
    "ス-パ-ガンダム": "슈퍼 건담",
    "ガンダムmkⅡ": "건담 Mk-II",
    "Gディフェンサ-": "G 디펜서",
    "Zガンダム": "Z 건담",
    "ウェイブライダ-": "웨이브라이더",
    "ZZガンダム": "ZZ 건담",
    "G-フォ-トレス": "G 포트리스",
    "√ガンダム": "뉴 건담",
    "F91": "F91",
    "NT-1アレックス": "NT-1 알렉스",
    "GP-01Fb": "GP-01Fb",
    "GP-02Aサイサリス": "GP-02A 사이살리스",
    "GP-03デンドロビウム": "GP-03 덴드로비움",
    "GP-03Sステイメン": "GP-03S 스테이멘",
    "ガンキャノン": "건캐논",
    "ザクⅢ改": "자쿠 III 개량형",
    "キュベレイ": "큐베레이",
    "ネモ": "네모",
    "パラス.アテネ": "팰러스 아테네",
    "ジェガン": "제간",
    "リ.ガズィ(BWS)": "리가지(BWS)",
    "リ.ガズィ": "리가지",
    "ゲッタ-1": "겟타 1",
    "ゲッタ-2": "겟타 2",
    "ゲッタ-3": "겟타 3",
    "ゲッタ-ドラゴン": "겟타 드래곤",
    "ゲッタ-ライガ-": "겟타 라이거",
    "ゲッタ-ポセイドン": "겟타 포세이돈",
    "マジンガ-Z": "마징가 Z",
    "マジンガ-Z(JS)": "마징가 Z(JS)",
    "マジンガ-Z(P)": "마징가 Z(P)",
    "グレ-トマジンガ-": "그레이트 마징가",
    "アフロダイA": "아프로다이 A",
    "ダイアナンA": "다이아난 A",
    "ボスボロット": "보스보로트",
    "ビュ-ナスA": "비너스 A",
    "コン.バトラ-V": "컴배틀러 V",
    "バトルジェット": "배틀 제트",
    "バトルクラッシャ-": "배틀 크래셔",
    "バトルタンク": "배틀 탱크",
    "バトルマリン": "배틀 마린",
    "バトルクラフト": "배틀 크래프트",
    "ダイタ-ン3": "다이탄 3",
    "ダイファイタ-": "다이파이터",
    "ダイタンク": "다이탱크",
    "クイン.マンサ": "퀸 만사",
    "サイコガンダム(MS)": "사이코 건담(MS)",
    "サイコガンダム(MA)": "사이코 건담(MA)",
    "サイコガンダムmkⅡ(MS)": "사이코 건담 Mk-II(MS)",
    "サイコガンダムmkⅡ(MA)": "사이코 건담 Mk-II(MA)",
    "ホワイトベ-ス": "화이트 베이스",
    "ザクⅡ": "자쿠 II",
    "グフ": "구프",
    "ドム": "돔",
    "ギャン": "걍",
    "ザクレロ": "자쿠레로",
    "ジオング": "지옹",
    "ジオング(首)": "지옹(머리)",
    "ハイゴック": "하이고그",
    "ズゴックE": "즈고크 E",
    "メッサ-ラ(MS)": "멧사라(MS)",
    "メッサ-ラ(MA)": "멧사라(MA)",
    "グシオスおⅢ": "구시오스 베타 III",
    "サイバスタ-": "사이버스타",
    "サイバ-ド": "사이버드",
    "グランゾン": "그랑존",
    "ネオ.グランゾン": "네오 그랑존",
    "ミネルバX": "미네르바 X",
    "グロッサムX2": "그롯삼 X2",
    "ブラッガ-S1": "브랏가 S1",
    "ザク改": "자쿠 개량형",
    "ドムⅡ": "돔 II",
    "旧ザク": "구형 자쿠",
    "アッザム": "앗잠",
    "エルメス": "엘메스",
    "マラサイ": "마라사이",
    "バ-ザム": "바잠",
    "ハンブラビ(MS)": "함브라비(MS)",
    "ハンブラビ(MA)": "함브라비(MA)",
    "アッシマ-(MS)": "앗시마(MS)",
    "アッシマ-(MA)": "앗시마(MA)",
    "バイアラン": "바이아란",
    "ガブスレイ(MS)": "가브스레이(MS)",
    "ガブスレイ(MA)": "가브스레이(MA)",
    "バウンド.ドック(MS)": "바운드 독(MS)",
    "バウンド.ドック(MA)": "바운드 독(MA)",
    "百式": "백식",
    "メタス(MS)": "메타스(MS)",
    "メタス(MA)": "메타스(MA)",
    "ブラウ.ブロ": "브라우 브로",
    "ビグロ": "비그로",
    "ケンプファ-": "캠퍼",
    "ヴァル.ヴァロ": "발 바로",
    "ガ-ベラ.テトラ": "가베라 테트라",
    "ノイエ.ジ-ル": "노이에 질",
    "キュベレイmkⅡ": "큐베레이 Mk-II",
    "ガルスJ": "갈루스 J",
    "ズサ": "즈사",
    "ハンマ.ハンマ": "함마 함마",
    "R.ジャジャ": "R 자자",
    "バウ(MS)": "바우(MS)",
    "バウ(MA)": "바우(MA)",
    "ドライセン": "드라이센",
    "ド-ベンウルフ": "도벤 울프",
    "ゲ-マルク": "게마르크",
    "ギラ.ド-ガ": "기라 도가",
    "ヤクト.ド-ガ": "야크트 도가",
    "サザビ-": "사자비",
    "ω.アジ-ル": "알파 아질",
    "ビギナ.ギナ": "비기나 기나",
    "ベルガ.ギロス": "베르가 기로스",
    "ラフレシア": "라플레시아",
    "サキ": "사키",
    "バド": "바드",
    "ザイ": "자이",
    "ズ-": "즈",
    "ゼンⅡ": "젠 II",
    "ダイ": "다이",
    "シグ": "시그",
    "ダブラスM2": "다브라스 M2",
    "ガラダK7": "가라다 K7",
    "トロスD7": "토로스 D7",
    "ラインX1": "라인 X1",
    "ジェノバM9": "제노바 M9",
    "スパルタンK5": "스파르탄 K5",
    "アブドラU6": "아브도라 U6",
    "グ-ル": "구울",
    "ブ-ド": "부드",
    "グラトニオス": "그라토니오스",
    "オベリウス": "오베리우스",
    "ピクドロン": "피크드론",
    "ギルギルガン": "길길간",
    "メカギルギルガン": "메카 길길간",
    "ドラゴノザウルス": "드라고노자우루스",
    "ヴァルシオン": "발시온",
    "マグマ獣ガルムス": "마그마수 가르무스",
    "ビッグガル-ダ": "빅 가루다",
    "ザンジバル": "잔지바르",
    "ミデア": "미데아",
    "ガウ": "가우",
    "ダブデ": "다브데",
    "トロイホ-ス": "트로이 호스",
    "グラ-フツェペリン": "그라프 체펠린",
    "ドゴス.ギア": "도고스 기어",
    "アレキサンドリア": "알렉산드리아",
    "ムサイ改": "개량형 무사이",
    "ア-ガマ": "아가마",
    "ス-ドリ": "수도리",
    "ネェル.ア-ガマ": "넬 아가마",
    "エンドラ": "엔드라",
    "サダラ-ン": "사다란",
    "レウル-ラ": "레우루라",
    "ラ-.カイラム": "라 카이람",
    "ザムス.ガル": "잠스 갈",
    "アルソス": "알소스",
    "ジャラガ": "자라가",
    "ゲッタ-Q": "겟타 Q",
    "メカ雷獣鬼": "메카 뇌수귀",
    "メカ胡蝶鬼": "메카 호접귀",
    "グレンダイザ-": "그렌다이저",
    "スペイザ-": "스페이저",
    "ダブルスペイザ-": "더블 스페이저",
    "マリンスペイザ-": "마린 스페이저",
    "ドリルスペイザ-": "드릴 스페이저",
    "グレンダイザ-(W)": "그렌다이저(W)",
    "グレンダイザ-(M)": "그렌다이저(M)",
    "グレンダイザ-(D)": "그렌다이저(D)",
    "誘導ミサイル": "유도 미사일",
    "固定砲台": "고정 포대",
    "発電ミラ-": "발전 미러",
    "GM": "GM",
    "ムサイ": "무사이",
    "ゲルググ": "겔구그",
    "リックディアス": "릭 디아스",
    "ディジェSE-R": "디제 SE-R",
    "カプ-ル": "카풀",
    "ダギ.イルス": "다기 이루스",
    "GP-01": "GP-01",
    "ドラッツェ": "드라체",
    "テキサスマック": "텍사스 맥",
    "暗黒大将軍": "암흑대장군",
    "グレイドン": "그레이돈",
    "ゲルググM": "겔구그 M",
    "ボング": "봉그",
    "プロトゲッタ-1": "프로토 겟타 1",
    "プロトゲッタ-2": "프로토 겟타 2",
    "プロトゲッタ-3": "프로토 겟타 3",
    "メカ鉄甲鬼": "메카 철갑귀",
    "ライディ-ン": "라이딘",
    "ゴッドバ-ド": "갓 버드",
    "ブル-ガ-": "블루거",
    "ドロ-メ": "드로메",
    "化石獣バストドン": "화석수 바스토돈",
    "ギルディ-ン": "길딘",
    "巨大シャ-キン": "거대 샤킨",
    "ガンテ": "간테",
    "メカガンテ": "메카 간테",
    "偽グレンダイザ-": "가짜 그렌다이저",
    "ギルギル": "길길",
    "キングゴリ": "킹고리",
    "ズメズメ": "즈메즈메",
    "ゴスゴス": "고스고스",
    "ガルガンチュワ": "가르강튀아",
    "パンタグリュエル": "팡타그뤼엘",
    "ヴァルシオ-ネ": "발시오네",
    "ヴァルシオン改": "개량형 발시온",
    "ガルガウ": "갈가우",
    "グレイタ-キン": "그레이타킨",
    "シルベルヴァント": "실베르빈트",
    "ドル-キン": "도르킨",
    "ディカステス": "디카스테스",
    "Gア-マ-": "G 아머",
    "ビグ.ザム": "빅 잠",
    "ラ-ディッシュ": "라디쉬",
    "オレアナ": "오레아나",
    "ドロス": "도로스",
    "ジ.O": "디 오",
    "コアブ-スタ-": "코어 부스터",
    "ザクⅢ": "자쿠 III",
    "シャア専用ザクⅡ": "샤아 전용 자쿠 II",
    "G.キャノン": "G 캐논",
    "ガザC(MS)": "가자 C(MS)",
    "ガザC(MA)": "가자 C(MA)",
    "ガザD(MS)": "가자 D(MS)",
    "ガザD(MA)": "가자 D(MA)",
    "ギャプラン(MS)": "갸프랑(MS)",
    "ギャプラン(MA)": "갸프랑(MA)",
    "ジャムル.フィン(MS)": "자무르 핀(MS)",
    "ジャムル.フィン(MA)": "자무르 핀(MA)",
    "ガ.ゾウム(MS)": "가 조움(MS)",
    "ガ.ゾウム(MA)": "가 조움(MA)",
    "ゲルググJ": "겔구그 J",
}


KANA: dict[str, str] = {
    # Foreign-sound combinations first; matching is longest-first below.
    "ウァ": "와", "ウィ": "위", "ウェ": "웨", "ウォ": "워",
    "ヴァ": "바", "ヴィ": "비", "ヴェ": "베", "ヴォ": "보", "ヴュ": "뷰",
    "ファ": "파", "フィ": "피", "フェ": "페", "フォ": "포", "フュ": "퓨",
    "ティ": "티", "トゥ": "투", "ディ": "디", "ドゥ": "두",
    "チェ": "체", "シェ": "셰", "ジェ": "제", "スィ": "시", "ズィ": "지",
    "ツァ": "차", "ツィ": "치", "ツェ": "체", "ツォ": "초",
    "キャ": "캬", "キュ": "큐", "キョ": "쿄",
    "ギャ": "갸", "ギュ": "규", "ギョ": "교",
    "シャ": "샤", "シュ": "슈", "ショ": "쇼",
    "ジャ": "자", "ジュ": "쥬", "ジョ": "죠",
    "チャ": "챠", "チュ": "츄", "チョ": "쵸",
    "ニャ": "냐", "ニュ": "뉴", "ニョ": "뇨",
    "ヒャ": "햐", "ヒュ": "휴", "ヒョ": "효",
    "ビャ": "뱌", "ビュ": "뷰", "ビョ": "뵤",
    "ピャ": "퍄", "ピュ": "퓨", "ピョ": "표",
    "ミャ": "먀", "ミュ": "뮤", "ミョ": "묘",
    "リャ": "랴", "リュ": "류", "リョ": "료",
    "クァ": "콰", "クィ": "퀴", "クェ": "퀘", "クォ": "쿼",
    "グァ": "과", "グィ": "귀", "グェ": "궤", "グォ": "궈",
    "イェ": "예",
    "あ": "아", "い": "이", "う": "우", "え": "에", "お": "오",
    "か": "카", "き": "키", "く": "쿠", "け": "케", "こ": "코",
    "が": "가", "ぎ": "기", "ぐ": "구", "げ": "게", "ご": "고",
    "さ": "사", "し": "시", "す": "스", "せ": "세", "そ": "소",
    "ざ": "자", "じ": "지", "ず": "즈", "ぜ": "제", "ぞ": "조",
    "た": "타", "ち": "치", "つ": "츠", "て": "테", "と": "토",
    "だ": "다", "ぢ": "지", "づ": "즈", "で": "데", "ど": "도",
    "な": "나", "に": "니", "ぬ": "누", "ね": "네", "の": "노",
    "は": "하", "ひ": "히", "ふ": "후", "へ": "헤", "ほ": "호",
    "ば": "바", "び": "비", "ぶ": "부", "べ": "베", "ぼ": "보",
    "ぱ": "파", "ぴ": "피", "ぷ": "푸", "ぺ": "페", "ぽ": "포",
    "ま": "마", "み": "미", "む": "무", "め": "메", "も": "모",
    "や": "야", "ゆ": "유", "よ": "요",
    "ら": "라", "り": "리", "る": "루", "れ": "레", "ろ": "로",
    "わ": "와", "を": "오",
    "ア": "아", "イ": "이", "ウ": "우", "エ": "에", "オ": "오",
    "カ": "카", "キ": "키", "ク": "쿠", "ケ": "케", "コ": "코",
    "ガ": "가", "ギ": "기", "グ": "구", "ゲ": "게", "ゴ": "고",
    "サ": "사", "シ": "시", "ス": "스", "セ": "세", "ソ": "소",
    "ザ": "자", "ジ": "지", "ズ": "즈", "ゼ": "제", "ゾ": "조",
    "タ": "타", "チ": "치", "ツ": "츠", "テ": "테", "ト": "토",
    "ダ": "다", "ヂ": "지", "ヅ": "즈", "デ": "데", "ド": "도",
    "ナ": "나", "ニ": "니", "ヌ": "누", "ネ": "네", "ノ": "노",
    "ハ": "하", "ヒ": "히", "フ": "후", "ヘ": "헤", "ホ": "호",
    "バ": "바", "ビ": "비", "ブ": "부", "ベ": "베", "ボ": "보",
    "パ": "파", "ピ": "피", "プ": "푸", "ペ": "페", "ポ": "포",
    "マ": "마", "ミ": "미", "ム": "무", "メ": "메", "モ": "모",
    "ヤ": "야", "ユ": "유", "ヨ": "요",
    "ラ": "라", "リ": "리", "ル": "루", "レ": "레", "ロ": "로",
    "ワ": "와", "ヲ": "오", "ヴ": "브",
}

ROMAN_NUMERALS = {"Ⅰ": "I", "Ⅱ": "II", "Ⅲ": "III", "Ⅳ": "IV"}


def add_final_consonant(text: str, jongseong: int) -> str:
    if not text:
        return text
    code = ord(text[-1])
    if 0xAC00 <= code <= 0xD7A3 and (code - 0xAC00) % 28 == 0:
        return text[:-1] + chr(code + jongseong)
    return text


def mechanical_transliteration(japanese: str) -> str:
    """Conservative kana fallback.  Every use is marked review_required."""

    result = ""
    position = 0
    geminate = False
    keys = sorted(KANA, key=len, reverse=True)
    while position < len(japanese):
        character = japanese[position]
        if character in "ッっ":
            geminate = True
            position += 1
            continue
        if character in "ンん":
            next_character = japanese[position + 1] if position + 1 < len(japanese) else ""
            final = 16 if next_character in "マミムメモバビブベボパピプペポ" else 4
            updated = add_final_consonant(result, final)
            result = updated if updated != result else result + ("ㅁ" if final == 16 else "ㄴ")
            position += 1
            continue
        if character in "ー-":
            # The source uses ASCII '-' as its long-vowel mark.  Preserve it
            # only in alphanumeric model codes such as GP-01.
            previous = japanese[position - 1] if position else ""
            following = japanese[position + 1] if position + 1 < len(japanese) else ""
            if previous.isascii() and following.isascii() and (
                previous.isalnum() or following.isalnum()
            ):
                result += "-"
            position += 1
            continue
        if character == "=":
            result += " "
            position += 1
            continue
        if character == ".":
            result += " "
            position += 1
            continue
        if character in ROMAN_NUMERALS:
            result += ROMAN_NUMERALS[character]
            position += 1
            continue

        matched = None
        for kana in keys:
            if japanese.startswith(kana, position):
                matched = kana
                break
        if matched is None:
            result += character
            position += 1
            continue
        syllable = KANA[matched]
        if geminate:
            # A small tsu normally doubles the following consonant.  Adding a
            # final ㅅ gives a useful approximation without inventing a Latin
            # romanisation layer.
            result = add_final_consonant(result, 19)
            geminate = False
        result += syllable
        position += len(matched)
    return re.sub(r"\s+", " ", result).strip()


def component_overrides() -> dict[str, str]:
    result: dict[str, str] = {}
    conflicts: set[str] = set()
    for japanese, korean in PILOT_FULL_COMPONENTS.items():
        ja_parts = japanese.split("=")
        ko_parts = korean.split("|")
        if len(ja_parts) != len(ko_parts):
            raise ValueError(f"pilot component-count mismatch: {japanese!r} / {korean!r}")
        for ja_part, ko_part in zip(ja_parts, ko_parts, strict=True):
            if ja_part in result and result[ja_part] != ko_part:
                conflicts.add(ja_part)
            else:
                result[ja_part] = ko_part
    for conflict in conflicts:
        result.pop(conflict, None)
    return result


def translation_for(
    kind: str,
    japanese: str,
    approved: dict[str, str],
    overlay_speakers: dict[str, str],
    components: dict[str, str],
) -> tuple[str, str, list[str]]:
    if japanese and set(japanese) == {"-"}:
        return japanese, "preserved_placeholder", []
    if japanese in approved:
        return approved[japanese], "approved_glossary", []
    if kind != "unit_name" and japanese in overlay_speakers:
        return overlay_speakers[japanese], "dialogue_overlay_unanimous_speaker", []

    if kind == "unit_name" and japanese in UNIT_OVERRIDES:
        return UNIT_OVERRIDES[japanese], "canonical_unit_catalogue", []
    if kind == "pilot_full_name":
        if japanese in PILOT_DIRECT:
            flags = ["unresolved_glyph_inferred_from_character_identity"] if "<G:" in japanese else []
            return PILOT_DIRECT[japanese], "canonical_pilot_catalogue", flags
        if japanese in PILOT_FULL_COMPONENTS:
            return PILOT_FULL_COMPONENTS[japanese].replace("|", " "), "canonical_pilot_catalogue", []
    if kind == "pilot_short_name" and japanese in PILOT_SHORT_DIRECT:
        return PILOT_SHORT_DIRECT[japanese], "canonical_pilot_short_catalogue", []
    if kind != "unit_name" and japanese in components:
        return components[japanese], "canonical_pilot_component", []
    if kind != "unit_name" and japanese in PILOT_DIRECT:
        return PILOT_DIRECT[japanese], "canonical_pilot_catalogue", []

    korean = mechanical_transliteration(japanese)
    flags = ["automatic_transliteration"]
    if JAPANESE_RE.search(korean) or "<G:" in korean:
        flags.append("untranslated_japanese_or_unresolved_glyph_remains")
    return korean, "automatic_transliteration", flags


def main() -> None:
    executable = EXECUTABLE.read_bytes()
    glyphs, glyph_rows = load_reviewed_glyphs()
    approved, glossary_document = load_approved_glossary()
    overlay_speakers = load_overlay_speaker_labels()
    components = component_overrides()

    tables_output: list[dict[str, Any]] = []
    all_rows: list[dict[str, Any]] = []
    for table in TABLES:
        expected_header = PSX_EXE_FILE_TO_RAM_BIAS + table["pointer_table_offset"]
        actual_header = u32(executable, table["header_offset"])
        if actual_header != expected_header:
            raise ValueError(
                f"{table['id']}: header changed: {actual_header:#x} != {expected_header:#x}"
            )
        if table["count"] * 4 != table["pointer_table_bytes"]:
            raise AssertionError(f"{table['id']}: inconsistent table byte count")

        rows: list[dict[str, Any]] = []
        for index in range(table["count"]):
            field = table["pointer_table_offset"] + index * 4
            relative = s32(executable, field)
            target = field + relative
            if target < table["pointer_table_offset"] + table["pointer_table_bytes"]:
                raise ValueError(f"{table['id']}[{index}]: target points inside pointer table")
            japanese, raw, unresolved = decode_record(
                executable, target, glyphs, glyph_rows
            )
            korean, source, flags = translation_for(
                table["kind"], japanese, approved, overlay_speakers, components
            )
            if unresolved and "unresolved_glyph_inferred_from_character_identity" not in flags:
                flags.append("source_contains_unresolved_glyph")
            if JAPANESE_RE.search(korean):
                flags.append("japanese_remains_in_korean")
            flags = list(dict.fromkeys(flags))
            row = {
                "index": index,
                "pointer_field_offset": field,
                "pointer_field_offset_hex": hex_offset(field),
                "relative_pointer": relative,
                "relative_pointer_hex": f"0x{relative & 0xFFFFFFFF:08X}",
                "target_offset": target,
                "target_offset_hex": hex_offset(target),
                "source_raw_hex": raw.hex(" ").upper(),
                "source_raw_sha256": sha256_bytes(raw),
                "japanese": japanese,
                "korean": korean,
                "translation_source": source,
                "review": {
                    "required": bool(flags),
                    "flags": flags,
                },
            }
            if unresolved:
                row["unresolved_glyphs"] = unresolved
            if japanese == "グシオスおⅢ":
                row["source_reconstruction"] = {
                    "reviewed_glyph_map_text": japanese,
                    "corrected_japanese": "グシオスβⅢ",
                    "reason": (
                        "glyph 0x04A is contextually beta in this unit label; the "
                        "reviewed map decodes the same bitmap as hiragana 'お'"
                    ),
                    "corroborating_source": "https://gesato.com/fc/suparobo2/code.html",
                }
            rows.append(row)
            all_rows.append({"table_id": table["id"], **row})

        tables_output.append(
            {
                **table,
                "header_offset_hex": hex_offset(table["header_offset"]),
                "pointer_table_offset_hex": hex_offset(table["pointer_table_offset"]),
                "header_ram_pointer": actual_header,
                "header_ram_pointer_hex": f"0x{actual_header:08X}",
                "rows": rows,
            }
        )

    source_counts = Counter(row["translation_source"] for row in all_rows)
    review_flags = Counter(
        flag for row in all_rows for flag in row["review"]["flags"]
    )
    short_rows = tables_output[0]["rows"]
    full_rows = tables_output[1]["rows"]
    source_identity_differences: list[dict[str, Any]] = []
    known_aliases: list[dict[str, Any]] = []
    true_consistency_errors: list[dict[str, Any]] = []
    alias_pairs = {("バ-ニィ", "バ-ナ-ド=ワイズマン")}
    for short, full in zip(short_rows, full_rows, strict=True):
        if set(short["japanese"]) == {"-"} or set(full["japanese"]) == {"-"}:
            continue
        short_ko = re.sub(r"[\s-]", "", short["korean"])
        full_ko = re.sub(r"[\s-]", "", full["korean"])
        if short_ko in full_ko:
            continue
        comparison = {
            "index": short["index"],
            "short_japanese": short["japanese"],
            "short_korean": short["korean"],
            "full_japanese": full["japanese"],
            "full_korean": full["korean"],
        }
        pair = (short["japanese"], full["japanese"])
        if pair in alias_pairs:
            comparison["reason"] = "Bernie is Bernard Wiseman's established nickname"
            known_aliases.append(comparison)
        elif short["japanese"] in full["japanese"].split("="):
            true_consistency_errors.append(comparison)
        else:
            comparison["reason"] = (
                "the retail short battle-label table and full pilot-data table "
                "assign different identities to this numeric slot"
            )
            source_identity_differences.append(comparison)
    if true_consistency_errors:
        raise ValueError(
            "related short/full pilot labels disagree: "
            + json.dumps(true_consistency_errors, ensure_ascii=False)
        )

    document = {
        "schema": "srwcb-second-ui-names-overlay-v1",
        "purpose": "Korean menu/status names for SECOND.WAR; no binary modifications",
        "source": {
            "executable_path": str(EXECUTABLE.relative_to(ROOT)).replace("\\", "/"),
            "executable_size": len(executable),
            "executable_sha256": sha256_bytes(executable),
            "glyph_map_path": str(GLYPH_MAP.relative_to(ROOT)).replace("\\", "/"),
            "glyph_map_sha256": sha256_file(GLYPH_MAP),
            "approved_glossary_path": str(GLOSSARY.relative_to(ROOT)).replace("\\", "/"),
            "approved_glossary_sha256": sha256_file(GLOSSARY),
            "approved_glossary_version": glossary_document.get("approval", {}).get("version"),
            "dialogue_overlay_path": str(DIALOGUE_OVERLAY.relative_to(ROOT)).replace("\\", "/"),
            "dialogue_overlay_sha256": sha256_file(DIALOGUE_OVERLAY),
            "translation_ledger_sha256": sha256_file(LEDGER),
        },
        "policy": {
            "priority": [
                "approved_glossary",
                "unanimous_speaker_label_from_dialogue_overlay",
                "canonical_local_catalogue",
                "automatic_kana_transliteration_with_review_flag",
            ],
            "placeholder_policy": "hyphen-only placeholders are preserved byte-for-label",
            "pilot_consistency": "full-name '=' components and short labels share one Korean component map",
            "unresolved_glyph_policy": "retain an explicit <G:0xNNN> source token and add a review flag",
        },
        "pilot_short_full_consistency": {
            "status": "pass",
            "related_translation_mismatches": len(true_consistency_errors),
            "known_aliases": known_aliases,
            "retail_source_identity_differences": source_identity_differences,
            "note": (
                "Numeric slots are retained exactly. A few retail slots refer to different "
                "characters in the battle short-label and pilot-data tables; those source "
                "differences must not be hidden by assigning the wrong Korean name."
            ),
        },
        "statistics": {
            "table_count": len(tables_output),
            "row_count": len(all_rows),
            "active_non_placeholder_rows": sum(
                row["translation_source"] != "preserved_placeholder" for row in all_rows
            ),
            "placeholder_rows": source_counts["preserved_placeholder"],
            "unique_japanese_names": len({row["japanese"] for row in all_rows}),
            "translation_source_counts": dict(sorted(source_counts.items())),
            "review_required_rows": sum(row["review"]["required"] for row in all_rows),
            "review_flag_counts": dict(sorted(review_flags.items())),
            "approved_glossary_entries_loaded": len(approved),
            "unanimous_overlay_speaker_labels_loaded": len(overlay_speakers),
        },
        "tables": tables_output,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(document["statistics"], ensure_ascii=False, indent=2))
    print(OUTPUT)


if __name__ == "__main__":
    main()
