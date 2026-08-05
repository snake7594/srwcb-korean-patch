#!/usr/bin/env python3
"""Build the reviewed Korean overlay for SECOND's executable UI scripts.

Only contiguous glyph spans are replaced. Renderer controls, dynamic fields,
layout decorations, and the kana name-entry grids remain byte-for-byte source
data. Every replacement carries the exact guarded source bytes.
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
if str(_P.TOOLS) not in _sys.path:
    _sys.path.insert(0, str(_P.TOOLS))
# ------------------------------------

import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from extract_dialogue_candidates import (
    MAPPING_POLICIES,
    iter_encoded_records,
    load_glyph_mapping,
)
from patch_second_exe_ui import parse_second_ui_vm_record


WORKSPACE = _P.WORK
PATCH_ROOT = _P.WORK
INVENTORY = PATCH_ROOT / "research" / "second_exe_ui_full_inventory.json"
GLYPH_MAP = PATCH_ROOT / "research" / "srwcb_embedded_font_mapping_reviewed.json"
OUTPUT = PATCH_ROOT / "translation_v2" / "second_ui_scripts_overlay.json"

JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")


PREVIEW_TRANSLATIONS: dict[int, list[str]] = {
    0: ["의 전멸"],
    2: ["아군의 전멸"],
    4: [
        "예고편",
        "Dr.J「콜로니에 핵 공격을 가하는 비인도적인",
        "전술도, 수단을 가리지 않는 승리를 위해서라면",
        "어쩔 수 없겠지. 그러므로 여기서 항복을 선언한다」",
    ],
    5: ["레이디「좋다, 항복을 받아들인다. 즉시 건담 흉내들은", "투항하라」"],
    6: ["히이로「……」"],
    7: ["브라이트「!! 저 소년은… 히이로 유이라고 했던가…」"],
    8: ["Dr.J「항복은 한다. 하지만 건담은 넘길 수 없다!", "반복한다! 항복한다! 하지만 건담은 못 넘긴다!!」"],
    9: ["히이로「임무… 확인!」"],
    10: ["레이디「뭐!? 자폭했다는 말인가!?」"],
    11: ["기렌「하만, 우리가 없는 동안 DC를 잘", "지켜 주었구나. 감사를 표하지」"],
    12: ["하만「…과분한 말씀입니다」"],
    13: ["키시리아「하지만 총수님, 현재 상황은 결코 순조롭다고", "할 수 없습니다. 서둘러 조직을 재편해야 합니다」"],
    14: ["기렌「그렇군…」"],
    15: ["레이디「각하… 이제 어떻게 하실 생각이십니까?」"],
    16: ["트레즈「시대가 나를 배제하려 하고 있다.", "그렇다면 나는 그 흐름을 거스를 수 없겠지」"],
    17: ["레이디「각하…」"],
    18: ["트레즈「이해해 다오, 레이디. 지금은 그때가 아니다」"],
    19: ["트레즈(젝스… 자네는 어디에 있나? 이대로라면", "지구연방도 티탄즈도 어리석은 자들의", "어리석은 행동을 허용하고 말겠지…)"],
    20: ["하타리「베스! 이것 좀 봐! 이 전천 좌표의 위치를…」"],
    21: ["베스「? 태양계 안이잖아? 하지만…」"],
    22: ["하타리「그래, 모니터에 보이는 건 아무리 봐도", "토성이야. 전천 좌표가 어긋난 모양이군」"],
    23: ["베스「고장인가?」"],
    24: ["하타리「모르겠어… 조사해 보지」"],
    25: ["카라라「!! 베스! 저건!!」"],
    26: ["베스「버프 클랜인가!? 말도 안 돼, 데스 드라이브를", "뒤쫓아 왔다는 건가!?」"],
    27: ["코스모「젠장! 카샤, 가자!!」"],
    28: ["다람「뜻밖의 행운이라고 해야겠군…」"],
    29: ["기제「예?」"],
    30: [
        "다람「이렇게 가까운 곳에서 로고다우 이성인의 함선을",
        "포착할 수 있었다는 사실 말이다.",
        "본국 정규군 추격대가 도착하기 전에",
        "우리의 공을 세울 수 있겠군」",
    ],
    31: ["기제「예」"],
    32: ["다람「잊지 마라. 우리는 정규군이 아니다.", "거신을 붙잡기 위해 봉급을 받는 몸이니까」"],
    33: ["「……?」"],
    34: [
        "「……?」", "「……?」", "「……?」", "「……?」", "「……?」", "「……?」",
        "「……?」신지「…오늘은 ", "님의 생일이죠.",
        "축하하려고 다 같이 케이크를 만들어 봤는데요…」",
    ],
    35: ["아스카「그래, 널 위해 우리가 정성을 다해", "만들었으니까 소원이나 빌고 얼른 불을", "끄라고!」"],
    36: ["신지「…잘도 말하네… 아스카는 초를", "꽂기만 했잖아…」"],
    37: ["아스카「뭐, 뭐야! 불만 있으면", "똑바로 말해!!」"],
    38: ["남자(싸운다)", "여자(싸우지 않는다)⟦G:AA7⟧JUMP-6신지「그럼 솔직히 말할게…」"],
    39: ["아스카「뭐, 뭐야! 대체 뭔데!」"],
    40: ["레이「…」"],
    41: ["신지「!? 아, 아스카, 오늘은 중요한 날이니까", "싸우지 말자…」"],
    42: ["아스카「뭐, 뭐야 그게!? …뭐, 좋아.", "이번만은 봐주겠어…」"],
    43: ["토우지「자, 싸움도 끝난 것 같으니", "님, 불을 꺼 주세요.", "지금 불 끌게요…」"],
    44: ["토우지「왜 그러세요? 왜 안 끄십니까?", "저희가 ", "님을 위해 준비한 겁니다.", "사양 말고 어서요, 어서」"],
    45: ["아스카「그래, 이 바보 말이 맞아.", "얼른 끄라니까!」"],
    46: ["아스카(왜 안 끄는 거야? 네가 안 끄겠다면", "내가 대신 꺼 주겠어…", "소원은 물론…", "카지 씨의 신부가 되게 해 주세요♥)"],
    47: ["토우지「소류, 네가 불 껐제?」"],
    48: ["아스카「너 바보야? 내가 그런 짓을", "할 리가 없잖아!!」"],
    49: ["신지「맞아, 토우지. 아무리 아스카라도", "그럴 리 없어. 아야나미도 그렇게 생각하지?」"],
    50: ["레이「…내가 껐어…」"],
    51: ["신지「그래, 솔직히 말하면 아스카는 언제나…」"],
    52: ["토우지「니들 부부싸움 할 거면", "이 방에서 나가라!」"],
    53: ["아스카「뭐, 뭐야! 시끄러워!!」"],
    54: ["레이「…」"],
    55: ["아스카「나가면 되잖아, 나가면!", "가자, 바보 신지!」"],
    56: ["신지「자, 잠깐만 아스카…」"],
    57: ["토우지「", "님, 저 둘은 내버려 두고", "불을 꺼 주세요. 지금 불 끌게요…」"],
    58: ["끈다", "끄지 않는다"],
    59: ["토우지「축하합니데이」"],
    60: ["시게루「축하해」"],
    61: ["레이「축하해」"],
    62: ["신지「아스카…」"],
    63: ["아스카「뭐!」"],
    64: ["신지「다들 케이크 먹는 것 같아」"],
    65: ["아스카「그래서 어쨌다는 거야」"],
    66: ["신지「나… ", "님께 축하한다는 말도", "못 했어. 싸움은 그만하고 방으로 돌아가자…」"],
    67: ["아스카「너 바보야? 창피해서 돌아갈 수 있을", "리가 없잖아! 축하하고 싶으면", "여기서 말하면 되잖아」"],
    68: ["신지「여, 여기서라니… 그건 못 하겠어…」"],
    69: ["아스카「기분 나빠…」"],
    70: ["토우지「왜 그러세요? 왜 안 끄십니까?", "저희가 ", "님을 위해 준비한 겁니다.", "사양 말고 어서요, 어서…」"],
    71: ["토우지「뭐고 신지, 부부싸움은 끝났나?」"],
    72: ["신지「…여러분, 죄송했습니다.", "보기 흉한 모습을 보여 드려서…」"],
    73: ["아스카(뭘 사과하는 거야!", "사과하면 다 되는 게 아니라고…", "꼴사나워)"],
    74: ["토우지「그만 궁시렁거리고", "얼른 이리 와서 ", "님을 다 같이", "축하하자 아이가」"],
    75: ["신지「응」"],
    76: ["토우지「자, 다 모였으니…", "님, 부탁합니데이」"],
    77: ["아스카「축하해」"],
    78: ["신지「축하해」"],
    79: ["아스카(…뭐야, 신지의 저 태도는. 왠지", "짜증 나. 게다가 왜 카지 씨는 없는 거야?", "이럴 때 카지 씨가 있어 줬다면…", "뭔가 속 시원한 일 없을까? …!?)"],
    80: ["「……?」", "「……?」", "「……?」", "「……?」", "「……?」", "「……?」", "「……?」", "「……?」아스카(아, 속 시원해…)"],
    81: ["토우지「소류, 네가 불 껐제?」"],
    82: ["아스카「뭐, 뭐야? 너 바보야?", "내가 그런 짓을 할 리가 없잖아!!」"],
    83: ["신지「맞아, 토우지. 아무리 아스카라도", "그럴 리 없어. 아야나미도 그렇게 생각하지?」"],
    84: ["레이「…내가 껐어…」"],
    85: ["이데「……」"],
    86: ["코스모「이, 이건…!?」"],
    87: ["카샤「이데의… 분노…?」"],
    88: ["졸리바「아무래도 이데가 우릴 버린 모양이군…」"],
    89: ["하타리「말도 안 돼… 난 아직 아무것도 못 했다고…」"],
    90: ["베스「코스모… 우리는… 모든 일이 너무 늦었던 건지도", "모르겠군…」"],
}


NESTED_TRANSLATIONS: list[str | None] = [
    "캐릭터 사전", "로봇 대도감", "마징가 Z", "극장판 마징가 Z", "그레이트 마징가",
    "겟타 로보", "겟타 로보 G", "초전자 로보 컴배틀러 V", "무적강인 다이탄 3",
    "성전사 단바인", "성전사 단바인 OVA", "중전기 엘가임", "기동전사 건담",
    "기동전사 Z 건담", "기동전사 건담 ZZ", "기동전사 건담 0080", "기동전사 건담 0083",
    "역습의 샤아", "기동전사 건담 F91", "전국마신 고쇼군", "초수기신 단쿠가",
    "신세기 에반게리온", "톱을 노려라!", "전설거신 이데온", "기동무투전 G건담",
    "신기동전기 건담 W", "오리지널", "용자 라이딘", "UFO 로보 그렌다이저", None,
    "차기작 예고편", "컴배틀러 V 합체", "마징가 Z 발진", "제트 스크랜더 발진",
    "비너스 A 발진", "그레이트 마징가 발진", "고쇼군 합신", "단쿠가 합체",
    "진 겟타 로보 변형", "그룬가스트 ~ 가스트랜더", "그룬가스트 ~ 윙가스트",
    "마징카이저 발진", "톱을 노려라 건버스터 발진", "에반게리온 초호기 발진",
    "최후의 사자 -카오루-", "겟타 1 변형", "겟타 2 변형", "겟타 3 변형", "다이탄 3",
    "두 사람의 만남", "석파 러브러브 천경권", "동방불패, 새벽에 죽다", "레인 모빌 트레이스",
    "초급패왕전영탄", "초급패왕전영탄", "궁극 석파천경권", "하이퍼 레프러칸",
    "하이퍼 라이네크", "하이퍼 즈와우스", "하이퍼 갈라바", "겟타 드래곤 변형",
    "겟타 라이거 변형", "겟타 포세이돈 변형", "FLYING IN THE SKY", "JUST COMMUNICATION",
    "엘가임 ~TIME FOR L-GAIM~", "THE WINNER", "단바인 난다", "사일런트 보이스",
    "겟타 로보!", "고쇼군 발진하라", "나는 그레이트 마징가", "마징가 Z", "Z의 테마",
    "하늘 나는 마징가 Z", "TIME TO COME", "컴 히어! 다이탄 3", "컴배틀러 V의 테마",
    "톱을 노려라! ~FLY HIGH~", "부활의 이데온", "코스모스에 그대와", "잔혹한 천사의 테제",
    "버닝 러브", "열풍! 질풍! 사이바스터", "날아라! 그렌다이저", "용자 라이딘", None,
    "「시간을 넘어서」", "「작전을 세울까?」", "「불러올까?」", "「계속할까?」",
    "「늠름한 샤아」", "「모빌슈트전 ~적기 습격」", "「사일런트 보이스」",
    "「FLYING IN THE SKY」", "「엘가임 ~TIME FOR L-GAIM~」", "「THE WINNER」",
    "「단바인 난다」", "「뉴 건담」", "「JUST COMMUNICATION」", "「F91 건담 출격」",
    "「명경지수」", "「투지여, 타올라라」", "「겟타 로보!」", "「마징가 Z」", "「Z의 테마」",
    "「하늘 나는 마징가 Z」", "「나는 그레이트 마징가」", "「컴 히어! 다이탄 3」",
    "「컴배틀러 V의 테마」", "「고쇼군 발진하라」", "「TIME TO COME」", "「버닝 러브」",
    "「부활의 이데온」", "「현이 난다」", "「코스모스에 그대와」",
    "「톱을 노려라! ~FLY HIGH~」", "「DECISIVE BATTLE」", "「잔혹한 천사의 테제」",
    "「VIOLENT BATTLE」", "「ARMAGEDDON」", "「열풍! 질풍! 사이바스터」", "「발시온」",
    "「불꽃의 중화체육교사」", "「물과 늪의 나라에서」", "「플래퍼 걸」",
    "「정조 미오의 존가라부시」", "「다크 프리즌」", "「발퀴레의 기행」",
    "「출격 준비할까?」", "「힘과 기술」", "「침공」", "「아득한 저편에서」",
    "「어둠의 사자」", "「100광년의 용기」", "「제노사이드 머신」",
    "「하트풀 메카닉」", "「마르스 베르세르크」", "「THE LAST JUDGMENT」", "「충격」",
    "「증원 부대 출현」", "「서브타이틀」", "「예감」", "「죽었어?」", "「레퀴엠」",
    "「저기, 작전을 세울까?」", "「교향곡 제9번 라단조 제4악장에서」", "「끝이에요오」",
    "「날아라! 그렌다이저」", "「용자 라이딘」", "「GROUND ZERO」", "「SILENT MOON」",
    "「STILLNESS FOOTSTEP」", "「THE UNIVERSE」", "「VIRTUOSITY」", "「WILDERNESS WAR」",
    "「랑그란의 바람」", "「정령의 가호」", "「달밤에」", "「속삭임」", "「CLOUD LAND」",
    "「DARK NEBULA」", "「잠깐의 휴식」", "「MEMORIAL DAY」", "「새벽」", "「잠시 휴식」",
    "「돌입」", "「전란」", "「마사키 서브타이틀」", "「류네 서브타이틀」", "「슈우 서브타이틀」",
]


MASTER_PHRASES: dict[str, str] = {
    "行動終了していないユニットが　　体あります": "행동을 마치지 않은 유닛이　　기 남았습니다",
    "フェイズを終了してもよろしいですか?": "페이즈를 종료하시겠습니까?",
    "フェイズを終了します": "페이즈를 종료합니다",
    "フェイズ終了": "페이즈 종료",
    "ユニット能力　パイロット能力　武器性能": "유닛 능력　파일럿 능력　무기 성능",
    "特殊能力　　　　シ-ルド": "특수 능력　　　　실드",
    "ユニット特別ボ-ナス": "유닛 특별 보너스",
    "全てのパラメ-タを最大まで改造したので,特別ボ-ナスがつきます。": "모든 능력치를 최대로 개조하여 특별 보너스를 받습니다.",
    "以下の中から1つだけ選択して下さい。": "다음 중 하나만 선택해 주세요.",
    "空.陸.海.宇のいずれかの地形適応をAにできる。": "공·육·해·우 중 하나의 지형 적응을 A로 올린다.",
    "ユニットの移動力+1。": "유닛 이동력 +1.",
    "ユニットのHP+2000。": "유닛 HP +2000.",
    "ユニットのEN+150。": "유닛 EN +150.",
    "ユニットの運動性+20。": "유닛 운동성 +20.",
    "ユニットの装甲+500。": "유닛 장갑 +500.",
    "の能力を表示する。": "의 능력을 표시한다.",
    "この武器のパラメ-タを最大まで改造したので,特別ボ-ナスが": "이 무기의 능력치를 최대로 개조하여 특별 보너스가",
    "つきます。資金(": "붙습니다. 자금(",
    ")を投入する事で以下の武器が付加": ")을 투입하면 다음 무기가 추가",
    "されます。投入しますか?": "됩니다. 투입하시겠습니까?",
    "制限をうける能力　　　()内は本来の能力": "제한받는 능력　　　( ) 안은 본래 능력",
    "この組み合わせでいいですか?": "이 조합으로 하시겠습니까?",
    "のせかえます。　よろしいですか?": "탑승시킵니다.　진행하시겠습니까?",
    "地形適応の": "지형 적응 ",
    "Aにします。": "A로 변경합니다.",
    "よろしいですか?": "진행하시겠습니까?",
    "どの能力を改造しますか?": "어떤 능력을 개조하시겠습니까?",
    "(最大で": "(최대 ",
    "段階まで)": "단계까지)",
    "名前と愛称を入力してください。": "이름과 애칭을 입력해 주세요.",
    "名前を入力してください。": "이름을 입력해 주세요.",
    "登録キャラクタ-の中から選択する": "등록 캐릭터에서 선택",
    "主人公設定の変更": "주인공 설정 변경",
    "L.Rボタンによりキャラクタ-変更": "L/R 버튼으로 캐릭터 변경",
    "登録キャラクタ-NO・": "등록 캐릭터 NO.",
    "残りの精神ポイント×2": "남은 정신 포인트 ×2",
    "次のレベルまであと": "다음 레벨까지",
    "出撃ユニット選択　あと": "출격 유닛 선택　남은 ",
    "全員の命令を一吝変更": "전원 명령 일괄 변경",
    "消費精神ポイント": "소비 정신 포인트",
    "精神検索一覧": "정신기 검색 목록",
    "精神検索": "정신기 검색",
    "精神ポイント": "정신 포인트",
    "作戦目的": "작전 목적",
    "勝利条件": "승리 조건",
    "敗北条件": "패배 조건",
    "インタ-ミッション": "인터미션",
    "デ-タセ-ブ": "데이터 저장",
    "ユニット改造": "유닛 개조",
    "武器改造": "무기 개조",
    "パイロット能力": "파일럿 능력",
    "ユニット能力": "유닛 능력",
    "武器性能": "무기 성능",
    "強化パ-ツ選択": "강화 파츠 선택",
    "強化パ-ツ装備": "강화 파츠 장착",
    "装備中のパ-ツ": "장착 중인 파츠",
    "強化パ-ツ": "강화 파츠",
    "次のマップへ": "다음 맵으로",
    "総タ-ン数": "총 턴 수",
    "反撃命令": "반격 명령",
    "マニュアル": "수동",
    "積極的に!": "적극적으로!",
    "効率よく!": "효율적으로!",
    "反撃するな!": "반격하지 마!",
    "パイロット:": "파일럿:",
    "ロボット:": "로봇:",
    "身代わり": "대신 맞기",
    "ケ-ブル": "케이블",
    "ダメ-ジ": "대미지",
    "EN攻消費": "공격 EN 소비",
    "EN防消費": "방어 EN 소비",
    "クリティカル": "크리티컬",
    "HP吸収": "HP 흡수",
    "イデオンゲ-ジ": "이데온 게이지",
    "サ-ベル回避": "사벨 회피",
    "ツバゼリ": "칼날 맞대기",
    "盾防": "방패 방어",
    "システム設定": "시스템 설정",
    "システム": "시스템",
    "戦闘BGM設定": "전투 BGM 설정",
    "特殊操作": "특수 조작",
    "+セレクト+スタ-トでリセット": "+셀렉트+스타트로 리셋",
    "セレクトを押し続けていれば": "셀렉트를 계속 누르면",
    "クイックコンティニュ-": "빠른 이어하기",
    "ボタン設定": "버튼 설정",
    "スピ-ドアップ": "속도 올리기",
    "全体マップ": "전체 맵",
    "部隊表": "부대 목록",
    "自軍ユニット送り": "다음 아군 유닛",
    "自軍ユニット戻し": "이전 아군 유닛",
    "敵軍ユニット送り": "다음 적군 유닛",
    "敵軍ユニット戻し": "이전 적군 유닛",
    "初期設定に戻す": "기본 설정 복원",
    "主人公設定": "주인공 설정",
    "これでいい": "이대로 확정",
    "名前を変更する": "이름 변경",
    "性別　男": "성별　남자",
    "性別　女": "성별　여자",
    "誕生日": "생일",
    "血液型": "혈액형",
    "ひらがな": "히라가나",
    "カタカナ": "가타카나",
    "空白　　決定": "공백　　결정",
    "ボ-ナス経験値": "보너스 경험치",
    "レベルアップ　レベル": "레벨 업　레벨",
    "ユニット修理": "유닛 수리",
    "修理費用": "수리 비용",
    "スタ-ト": "시작",
    "ロ-ド": "불러오기",
    "コンティニュ-": "이어하기",
    "オプションモ-ド": "옵션 모드",
    "オプション": "옵션",
    "本体RAM": "본체 RAM",
    "カ-トリッジRAM": "카트리지 RAM",
    "新規デ-タ": "새 데이터",
    "このデ-タは使用できません。": "이 데이터는 사용할 수 없습니다.",
    "話までクリア": "화까지 클리어",
    "のりかえ": "갈아타기",
    "攻撃力": "공격력",
    "必要気力": "필요 기력",
    "消費EN": "소비 EN",
    "必要技能": "필요 기능",
    "クリティカル補正": "크리티컬 보정",
    "装弾数": "장탄 수",
    "収録率": "수록률",
    "登場作品": "등장 작품",
    "声優": "성우",
    "デモセレクト": "데모 선택",
    "カラオケモ-ド": "가라오케 모드",
    "サウンドセレクト": "사운드 선택",
    "キャラクタ-事典": "캐릭터 사전",
    "ロボット大図襤": "로봇 대도감",
    "ステレオ": "스테레오",
    "モノラル": "모노",
    "パイロット": "파일럿",
    "特殊技能": "특수 기능",
    "精神コマンド": "정신기",
    "精神": "정신기",
    "特殊能力": "특수 능력",
    "武器名": "무기명",
    "射程": "사거리",
    "弾数": "탄 수",
    "命中率": "명중률",
    "命中": "명중",
    "防御": "방어",
    "回避": "회피",
    "反応": "반응",
    "技量": "기량",
    "格闘": "격투",
    "射撃": "사격",
    "移動力": "이동력",
    "運動性": "운동성",
    "装甲": "장갑",
    "限界": "한계",
    "地形　空": "지형　공중",
    "地形": "지형",
    "空": "공중",
    "陸": "육지",
    "海": "바다",
    "宇": "우주",
    "サイズ": "크기",
    "タイプ": "타입",
    "レベル": "레벨",
    "気力": "기력",
    "経験値": "경험치",
    "資金": "자금",
    "タ-ン数": "턴 수",
    "セ-ブ": "저장",
    "費用": "비용",
    "表示": "표시",
    "特技": "특기",
    "結果:": "결과:",
    "武器:": "무기:",
    ":アニメ:": ":애니메이션:",
    "スクエア": "이동 범위",
    "サウンド": "사운드",
    "決定": "결정",
    "キャンセル": "취소",
    "名前": "이름",
    "愛称": "애칭",
    "性別": "성별",
    "性格": "성격",
    "顔": "얼굴",
    "月": "월",
    "日": "일",
    "型": "형",
    "修理": "수리",
    "補給": "보급",
    "説得": "설득",
    "能力": "능력",
    "待機": "대기",
    "攻撃": "공격",
    "移動": "이동",
    "発進": "발진",
    "搭載": "탑재",
    "合体": "합체",
    "分離": "분리",
    "変形": "변형",
    "切断": "절단",
    "パ-ツ": "파츠",
    "地上": "지상",
    "空中": "공중",
    "水中": "수중",
    "地中": "지중",
    "はい": "예",
    "いいえ": "아니요",
    "しますか?": "하시겠습니까?",
    "はずす": "해제",
    "第": "제",
    "話「": "화「",
    "」までクリア": "」까지 클리어",
    "話": "화",
    "全長": "전장",
    "重量": "중량",
}

MASTER_INDEX_PHRASES: dict[int, dict[str, str]] = {
    68: {"を": "을"},
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def glyph_text(index: int, mapping: Any) -> str:
    if index == 0:
        return "　"
    row = mapping.rows[index]
    character = row.get("character")
    confidence = str(row.get("confidence", "unresolved"))
    if character is not None and confidence in MAPPING_POLICIES["reviewed"]:
        return character
    return f"⟦G:{index:03X}⟧"


def glyph_runs(
    raw: bytes,
    mapping: Any,
    *,
    second_ui_vm: bool = False,
) -> list[dict[str, Any]]:
    normalised: list[dict[str, Any]] = []
    if second_ui_vm:
        end, tokens = parse_second_ui_vm_record(raw, 0, len(raw))
        if end != len(raw):
            raise ValueError("inventory raw_hex is not one complete SECOND UI VM record")
        for token in tokens:
            if token.kind != "glyph":
                normalised.append({"type": token.kind})
                continue
            index = (
                token.raw[0]
                if len(token.raw) == 1
                else ((token.raw[0] - 0xEB) << 8) | token.raw[1]
            )
            normalised.append(
                {
                    "type": "glyph",
                    "offset": token.start,
                    "source": token.raw,
                    "index": index,
                }
            )
    else:
        parsed = list(iter_encoded_records(raw, 0, len(raw)))
        if len(parsed) != 1 or parsed[0].end != len(raw):
            raise ValueError("inventory raw_hex is not one complete FF record")
        for token in parsed[0].tokens:
            normalised.append(
                {
                    "type": token["type"],
                    "offset": token.get("offset"),
                    "source": bytes.fromhex(token.get("raw_hex", "")),
                    "index": token.get("index"),
                }
            )

    runs: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for token in normalised:
        if token["type"] == "glyph":
            start = token["offset"]
            source = token["source"]
            current.append(
                {
                    "start": start,
                    "end": start + len(source),
                    "index": token["index"],
                    "text": glyph_text(token["index"], mapping),
                }
            )
            continue
        if current:
            while current and current[0]["index"] == 0:
                current.pop(0)
            while current and current[-1]["index"] == 0:
                current.pop()
            if current:
                runs.append({"tokens": current, "text": "".join(row["text"] for row in current)})
            current = []
    if current:
        while current and current[0]["index"] == 0:
            current.pop(0)
        while current and current[-1]["index"] == 0:
            current.pop()
        if current:
            runs.append({"tokens": current, "text": "".join(row["text"] for row in current)})
    return runs


def full_run_replacements(
    raw: bytes,
    runs: list[dict[str, Any]],
    translations: list[str],
) -> list[dict[str, Any]]:
    if len(runs) != len(translations):
        raise ValueError(f"visible run mismatch: source={len(runs)}, Korean={len(translations)}")
    output: list[dict[str, Any]] = []
    for run, korean in zip(runs, translations, strict=True):
        tokens = run["tokens"]
        source_text = run["text"]
        marker_re = re.compile(r"⟦G:[0-9A-F]{3}⟧")
        source_markers = marker_re.findall(source_text)
        korean_markers = marker_re.findall(korean)
        if source_markers != korean_markers:
            raise ValueError(
                f"unresolved glyph markers must be preserved exactly: "
                f"{source_text!r} -> {korean!r}"
            )
        source_parts = marker_re.split(source_text)
        korean_parts = marker_re.split(korean)
        source_cursor = 0
        char_to_token: dict[int, int] = {0: 0}
        char_cursor = 0
        for token_index, token in enumerate(tokens, 1):
            char_cursor += len(token["text"])
            char_to_token[char_cursor] = token_index
        for part_index, (source_part, korean_part) in enumerate(
            zip(source_parts, korean_parts, strict=True)
        ):
            part_start = source_cursor
            part_end = part_start + len(source_part)
            if source_part:
                if part_start not in char_to_token or part_end not in char_to_token:
                    raise ValueError("full-run translation split is not on glyph boundaries")
                selected = tokens[char_to_token[part_start]:char_to_token[part_end]]
                start, end = selected[0]["start"], selected[-1]["end"]
                output.append(
                    {
                        "relative_start": start,
                        "relative_end": end,
                        "source_hex": raw[start:end].hex(" ").upper(),
                        "source_sha256": sha256_bytes(raw[start:end]),
                        "japanese_text": source_part,
                        "korean_text": korean_part,
                    }
                )
            source_cursor = part_end
            if part_index < len(source_markers):
                source_cursor += len(source_markers[part_index])
    return output


def phrase_replacements(
    raw: bytes,
    runs: list[dict[str, Any]],
    entry_index: int,
) -> list[dict[str, Any]]:
    phrase_map = dict(MASTER_PHRASES)
    phrase_map.update(MASTER_INDEX_PHRASES.get(entry_index, {}))
    phrases = sorted(phrase_map, key=len, reverse=True)
    output: list[dict[str, Any]] = []
    for run in runs:
        text = run["text"]
        tokens = run["tokens"]
        boundaries: dict[int, int] = {0: 0}
        cursor = 0
        for token_index, token in enumerate(tokens, 1):
            cursor += len(token["text"])
            boundaries[cursor] = token_index
        occupied = [False] * len(text)
        matches: list[tuple[int, int, str, str]] = []
        for phrase in phrases:
            search = 0
            while True:
                start = text.find(phrase, search)
                if start < 0:
                    break
                end = start + len(phrase)
                search = start + 1
                if start not in boundaries or end not in boundaries:
                    continue
                if any(occupied[start:end]):
                    continue
                for position in range(start, end):
                    occupied[position] = True
                matches.append((start, end, phrase, phrase_map[phrase]))
        for start, end, japanese, korean in sorted(matches):
            first = boundaries[start]
            final = boundaries[end]
            selected = tokens[first:final]
            byte_start, byte_end = selected[0]["start"], selected[-1]["end"]
            output.append(
                {
                    "relative_start": byte_start,
                    "relative_end": byte_end,
                    "source_hex": raw[byte_start:byte_end].hex(" ").upper(),
                    "source_sha256": sha256_bytes(raw[byte_start:byte_end]),
                    "japanese_text": japanese,
                    "korean_text": korean,
                }
            )
    output.sort(key=lambda row: row["relative_start"])
    for left, right in zip(output, output[1:]):
        if left["relative_end"] > right["relative_start"]:
            raise AssertionError("overlapping master phrase replacements")
    return output


def uncovered_japanese_glyphs(
    runs: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return Japanese/Han glyph tokens not owned by a replacement span."""

    output: list[dict[str, Any]] = []
    for run in runs:
        for token in run["tokens"]:
            covered = any(
                row["relative_start"] <= token["start"]
                and token["end"] <= row["relative_end"]
                for row in replacements
            )
            # U+30FB is punctuation retained in the safe low font, not an
            # untranslated Japanese-language label.
            japanese = any(
                character != "・" and JAPANESE_RE.fullmatch(character)
                for character in token["text"]
            )
            if japanese and not covered:
                output.append(token)
    return output


def common_fields(record: dict[str, Any], asset_id: str, scope: str) -> dict[str, Any]:
    return {
        "asset_id": asset_id,
        "scope": scope,
        "exe": "SECOND/SECOND.WAR",
        "pointer_field": record.get("pointer_field"),
        "source_offset": record["source_offset"],
        "source_offset_hex": record["source_offset_hex"],
        "end_offset_exclusive": record["end_offset_exclusive"],
        "raw_sha256": record["raw_sha256"],
        "raw_hex": record["raw_hex"],
        "control_signature": record["control_signature"],
        "status": "approved",
    }


def sanitise_public_overlay(
    document: dict[str, Any],
    public_inventory_bytes: bytes,
) -> dict[str, Any]:
    """Remove Japanese/source bytes while retaining span SHA guards."""

    public = copy.deepcopy(document)
    public["source_inventory"] = {
        "path": "translation/second_ui_inventory.json",
        "sha256": sha256_bytes(public_inventory_bytes),
        "format": document["source_inventory"]["format"],
    }
    for asset in public["assets"].values():
        for row in asset["records"]:
            row.pop("raw_hex", None)
            for replacement in row.get("replacements", []):
                replacement.pop("source_hex", None)
                replacement.pop("japanese_text", None)
    return public


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--public-output", type=Path)
    parser.add_argument("--public-inventory", type=Path)
    args = parser.parse_args()
    if (args.public_output is None) != (args.public_inventory is None):
        parser.error("--public-output and --public-inventory must be supplied together")

    inventory_bytes = INVENTORY.read_bytes()
    inventory = json.loads(inventory_bytes.decode("utf-8"))
    mapping = load_glyph_mapping(GLYPH_MAP)
    assets: dict[str, Any] = {}

    preview_records: list[dict[str, Any]] = []
    for source in inventory["common_preview_pool"]["records"]:
        raw = bytes.fromhex(source["raw_hex"])
        if sha256_bytes(raw) != source["raw_sha256"]:
            raise ValueError(f"preview {source['sequential_index']}: raw hash mismatch")
        runs = glyph_runs(raw, mapping)
        if [row["text"] for row in runs] != source["visible_runs"]:
            raise ValueError(f"preview {source['sequential_index']}: visible runs changed")
        index = source["sequential_index"]
        row = common_fields(
            source, inventory["common_preview_pool"]["asset_id"], "all_five_executables"
        )
        row.update(
            {
                "entry_index": None,
                "sequential_index": index,
                "relocation_group": "common_preview_and_conditions_pool_91_sequential_records",
                "translation_target": source["translation_target"],
                "replacements": full_run_replacements(raw, runs, PREVIEW_TRANSLATIONS[index])
                if source["translation_target"] else [],
            }
        )
        if source["translation_target"] and not row["replacements"]:
            raise ValueError(f"preview {index}: untranslated target")
        preview_records.append(row)
    if set(PREVIEW_TRANSLATIONS) != {
        row["sequential_index"] for row in inventory["common_preview_pool"]["records"]
        if row["translation_target"]
    }:
        raise ValueError("preview translation key coverage mismatch")
    assets[inventory["common_preview_pool"]["asset_id"]] = {
        "required_record_count": 91,
        "relocation_group": "common_preview_and_conditions_pool_91_sequential_records",
        "records": preview_records,
    }

    nested_source = inventory["common_music_demo_pool"]["records"]
    if len(NESTED_TRANSLATIONS) != len(nested_source):
        raise ValueError("nested translation list length mismatch")
    nested_records: list[dict[str, Any]] = []
    for source, korean in zip(nested_source, NESTED_TRANSLATIONS, strict=True):
        raw = bytes.fromhex(source["raw_hex"])
        if sha256_bytes(raw) != source["raw_sha256"]:
            raise ValueError(f"nested {source['sequential_index']}: raw hash mismatch")
        runs = glyph_runs(raw, mapping)
        if [row["text"] for row in runs] != source["visible_runs"]:
            raise ValueError(f"nested {source['sequential_index']}: visible runs changed")
        structural_empty = not runs
        if structural_empty != (korean is None):
            raise ValueError(f"nested {source['sequential_index']}: missing or extra Korean text")
        row = common_fields(
            source, inventory["common_music_demo_pool"]["asset_id"], "all_five_executables"
        )
        row.update(
            {
                "entry_index": source["nested_indices"],
                "sequential_index": source["sequential_index"],
                "pointer_fields": source["pointer_fields"],
                "relocation_group": "common_music_demo_title_pool_171_records",
                "translation_target": not structural_empty,
                "status": "structural_empty" if structural_empty else "approved",
                "replacements": [] if structural_empty else full_run_replacements(raw, runs, [korean]),
            }
        )
        nested_records.append(row)
    assets[inventory["common_music_demo_pool"]["asset_id"]] = {
        "required_record_count": 171,
        "relocation_group": "common_music_demo_title_pool_171_records",
        "records": nested_records,
    }

    master_records: list[dict[str, Any]] = []
    master_targets = [
        row for row in inventory["second_ui_master"]["records"] if row["translation_target"]
    ]
    for source in master_targets:
        raw = bytes.fromhex(source["raw_hex"])
        if sha256_bytes(raw) != source["raw_sha256"]:
            raise ValueError(f"master {source['entry_index']}: raw hash mismatch")
        runs = glyph_runs(raw, mapping, second_ui_vm=True)
        if [row["text"] for row in runs] != source["visible_runs"]:
            raise ValueError(f"master {source['entry_index']}: visible runs changed")
        replacements = phrase_replacements(raw, runs, source["entry_index"])
        if not replacements and source["entry_index"] != 103:
            raise ValueError(f"master {source['entry_index']}: target has no translation span")
        residual = uncovered_japanese_glyphs(runs, replacements)
        # These are intentionally retained by the approved 73-row overlay:
        # dynamic counters/decorations (15/38), sortie/save/parts labels whose
        # source spans were not approved (17/71/73/81/83/91/93), and the kana
        # name-entry grids (47/48/53/54).  Keep the list explicit so any new
        # untranslated Japanese run still fails generation.
        approved_residual_entries = {
            15, 17, 38, 47, 48, 53, 54, 71, 73, 81, 83, 91, 93,
        }
        if residual and source["entry_index"] not in approved_residual_entries:
            rendered = "".join(token["text"] for token in residual)
            raise ValueError(
                f"master {source['entry_index']}: uncovered Japanese/Han glyphs {rendered!r}"
            )
        row = common_fields(
            source, inventory["second_ui_master"]["asset_id"], "SECOND_only"
        )
        row.update(
            {
                "entry_index": source["entry_index"],
                "category": source["category"],
                "relocation_group": "second_ui_script_master_77_pointer_records",
                "translation_target": True,
                "status": "verified_no_japanese_label" if not replacements else "approved",
                "replacements": replacements,
            }
        )
        master_records.append(row)
    if len(master_records) != 77 or {row["entry_index"] for row in master_records} != set(
        inventory["second_ui_master"]["translation_target_indices"]
    ):
        raise ValueError("SECOND master target coverage mismatch")
    required_new_span_counts = {10: 2, 11: 9, 20: 4, 21: 4}
    actual_new_span_counts = {
        row["entry_index"]: len(row["replacements"])
        for row in master_records
        if row["entry_index"] in required_new_span_counts
    }
    if actual_new_span_counts != required_new_span_counts:
        raise ValueError(
            f"SECOND master new-span coverage mismatch: {actual_new_span_counts!r}"
        )
    master_span_count = sum(len(row["replacements"]) for row in master_records)
    if master_span_count != 394:
        raise ValueError(f"SECOND master replacement count {master_span_count} != 394")
    assets[inventory["second_ui_master"]["asset_id"]] = {
        "required_record_count": 77,
        "relocation_group": "second_ui_script_master_77_pointer_records",
        "records": master_records,
    }

    all_records = [row for asset in assets.values() for row in asset["records"]]
    replacement_count = sum(len(row["replacements"]) for row in all_records)
    for row in all_records:
        for replacement in row["replacements"]:
            if not replacement["korean_text"].strip():
                raise ValueError("blank Korean replacement")
            if JAPANESE_RE.search(replacement["korean_text"]):
                raise ValueError(
                    f"Japanese remains in Korean replacement: {replacement['korean_text']!r}"
                )
    document = {
        "schema": "srwcb-second-ui-scripts-overlay-v1",
        "source_inventory": {
            "path": "research/second_exe_ui_full_inventory.json",
            "sha256": sha256_bytes(inventory_bytes),
            "format": inventory["format"],
        },
        "policy": {
            "exact_source_span_guard": True,
            "control_bytes_preserved": True,
            "dynamic_fields_preserved": True,
            "fixed_slot_truncation_allowed": False,
            "kana_name_entry_grids_preserved": True,
            "structural_decoration_glyphs_preserved": True,
        },
        "statistics": {
            "asset_count": len(assets),
            "record_count": len(all_records),
            "preview_record_count": len(preview_records),
            "nested_record_count": len(nested_records),
            "second_master_target_count": len(master_records),
            "replacement_span_count": replacement_count,
            "partial": False,
        },
        "assets": assets,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.public_output is not None and args.public_inventory is not None:
        public_inventory_bytes = args.public_inventory.read_bytes()
        public_document = sanitise_public_overlay(document, public_inventory_bytes)
        args.public_output.write_text(
            json.dumps(public_document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(document["statistics"], ensure_ascii=False, indent=2))
    print(OUTPUT)
    if args.public_output is not None:
        print(args.public_output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
