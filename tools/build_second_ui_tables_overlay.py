#!/usr/bin/env python3
"""Build the reviewed SECOND.WAR menu/status-table Korean overlay.

This generator is deliberately data-only: it reads the immutable executable
inventory and emits a translation overlay.  It never edits SECOND.WAR or an
image.  Keeping the reviewed strings here makes coverage and terminology
checks reproducible before a later relocation/compiler pass consumes them.
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
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = _P.WORK
INVENTORY = ROOT / "korean_patch/research/second_exe_ui_full_inventory.json"
GLOSSARY = ROOT / "korean_patch/research/translation_v2/glossary_candidates.json"
DIALOGUE_OVERLAY = ROOT / "korean_patch/translation_v2/second_translation_overlay.json"
OUTPUT = ROOT / "korean_patch/translation_v2/second_ui_tables_overlay.json"

TARGET_TABLES = (
    "terrain_combinations",
    "terrain_names",
    "spirit_commands",
    "enhancement_parts",
    "weapon_names",
    "pilot_skills",
    "unit_abilities",
    "scenario_titles",
)

EXPECTED_TARGET_COUNTS = {
    "terrain_combinations": 15,
    "terrain_names": 113,
    "spirit_commands": 90,
    "enhancement_parts": 44,
    "weapon_names": 1408,
    "pilot_skills": 37,
    "unit_abilities": 21,
    "scenario_titles": 26,
}

JAPANESE_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
UNRESOLVED_GLYPH_RE = re.compile(r"(?:⟦G:[0-9A-F]+⟧|<G:[^>]+>)")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_pairs(block: str) -> dict[str, str]:
    """Parse a compact source<TAB>Korean review catalogue.

    ``<NL>`` stands for a layout-significant source line break.  Leading and
    trailing spaces are meaningful (terrain-combination alignment), so only
    wholly empty/comment lines are ignored.
    """

    result: dict[str, str] = {}
    for line_number, line in enumerate(block.splitlines(), 1):
        if not line or line.lstrip().startswith("#"):
            continue
        if "\t" not in line:
            raise ValueError(f"catalogue line {line_number} has no TAB: {line!r}")
        source, korean = line.split("\t", 1)
        source = source.replace("<NL>", "\n")
        korean = korean.replace("<NL>", "\n")
        if source in result:
            raise ValueError(f"duplicate catalogue source: {source!r}")
        result[source] = korean
    return result


TERRAIN_COMBINATIONS = parse_pairs(r"""
⟦G:3FF⟧⟦G:3FF⟧⟦G:3FF⟧陸	   육
⟦G:3FF⟧⟦G:3FF⟧⟦G:3FF⟧空	   공
⟦G:3FF⟧⟦G:3FF⟧水陸	  수육
⟦G:3FF⟧⟦G:3FF⟧宇宙	  우주
⟦G:3FF⟧⟦G:3FF⟧空陸	  공육
⟦G:3FF⟧⟦G:3FF⟧水空	  수공
⟦G:3FF⟧水陸空	 수육공
⟦G:3FF⟧陸地中	 육지중
空陸地中	공육지중
⟦G:3FF⟧⟦G:3FF⟧⟦G:3FF⟧水	   수
⟦G:3FF⟧空地中	 공지중
""")


TERRAIN_NAMES = parse_pairs(r"""
道路	도로
橋	다리
街	시가지
平原	평원
林	수풀
森	숲
山	산
海	바다
深海	심해
川	강
砂地	모래땅
軍事基地	군사 기지
光子力研究所	광자력 연구소
新早乙女研究所	신 사오토메 연구소
南原コネクション	난바라 커넥션
砂漠	사막
⟦G:70C⟧裂	균열
高層ビル	고층 빌딩
国会議事堂	국회의사당
宇宙空間	우주 공간
暗礁空域	암초 공역
残がい	잔해
コロニ-残がい	콜로니 잔해
コロニ-	콜로니
ラビアンロ-ズ	라비앙 로즈
地球	지구
火星	화성
月	달
フォヴォス	포보스
ダイモス	데이모스
コロニ-レ-ザ-	콜로니 레이저
ミラ-	미러
ア.バオア.ク-	아 바오아 쿠
ソロモン	솔로몬
アクシズ	액시즈
ルナツ-	루나 2
クレ-タ-	크레이터
月面都市	월면 도시
クレバス	크레바스
月面	월면
丘	언덕
湖	호수
ガケ	절벽
斜面	비탈
進入不可	진입 불가
壁	벽
エネルギ-タンク	에너지 탱크
床	바닥
MSトレ-ラ-	MS 트레일러
ビル	빌딩
ガレキ	잔해
空	하늘
雲	구름
荒地	황무지
低木	관목
岩	바위
火星基地	화성 기지
ギアナ高地	기아나 고지
空中	공중
滝	폭포
土星	토성
転送装置	전송 장치
神面岩	신면암
""")


SPIRIT_COMMANDS = parse_pairs(r"""
根性	근성
補給	보급
ド根性	초근성
熱血	열혈
友情	우정
必中	필중
愛	사랑
ひらめき	번뜩임
てかげん	봐주기
気合	기합
幸運	행운
信頼	신뢰
加速	가속
覚醒	각성
集中	집중
激励	격려
再動	재동
復活	부활
隠れ身	은신
脱力	탈력
自爆	자폭
みがわり	대신 맞기
かく乱	교란
鉄壁	철벽
⟦G:562⟧	혼
努力	노력
挑発	도발
夢	꿈
奇跡	기적
偵察	정찰
根	근
補	보
ド根	초근
熱	열
友	우
必	필
閃	섬
手	수
気	기
幸	행
信	신
加	가
覚	각
集	집
励	격
再	재
復	부
隠	은
脱	탈
爆	폭
身	대
撹	교
鉄	철
努	노
挑	도
奇	기
偵	정
自分の最大HPの30%を回復します。	자신의 최대 HP의 30%를 회복합니다.
指定したユニットのエネルギ-,残弾を最大まで補給します。<NL>ただし,補給されたパイロットの気力は-10されます。	지정한 유닛의 에너지와 잔탄을 최대로 보급합니다.<NL>단, 보급받은 파일럿의 기력은 10 감소합니다.
自分のHPを最大まで回復します。	자신의 HP를 최대로 회복합니다.
一度だけ,敵に与えるダメ-ジが2倍になります。	한 번만 적에게 주는 피해가 2배가 됩니다.
すべての味方ユニットのHPを,最大HPの50%分回復します。	모든 아군 유닛의 HP를 최대 HP의 50%만큼 회복합니다.
1タ-ンの間,攻撃の命中率が100%になります。ただし,相手が<NL>「ひらめき」を使っていた場合,「ひらめき」が優先されます。	1턴 동안 공격 명중률이 100%가 됩니다. 단, 상대가<NL>‘번뜩임’을 사용했다면 ‘번뜩임’이 우선됩니다.
すべての味方ユニットのHPを,100%回復します。	모든 아군 유닛의 HP를 100% 회복합니다.
一回だけ,敵の攻撃を完全回避します。	한 번만 적의 공격을 완전히 회피합니다.
敵ユニットを倒さずに,HPを10だけ残します。ただし,相手より<NL>技量が上回っていなければ無効になります。	적 유닛을 격추하지 않고 HP를 10만 남깁니다. 단, 상대보다<NL>기량이 높지 않으면 무효입니다.
自分の気力を+10します。	자신의 기력을 10 올립니다.
次の戦闘で得られる資金を2倍にしてくれます。	다음 전투에서 얻는 자금이 2배가 됩니다.
指定した味方ユニットのHPを,最大HPの30%回復します。	지정한 아군 유닛의 HP를 최대 HP의 30%만큼 회복합니다.
一度だけ移動力に+3されます。	한 번만 이동력이 3 증가합니다.
一回分行動回数が増えます。	행동 횟수가 한 번 늘어납니다.
1タ-ンの間,命中率,回避率が30%アップします。	1턴 동안 명중률과 회피율이 30% 상승합니다.
指定した味方ユニットの気力を10あげます。	지정한 아군 유닛의 기력을 10 올립니다.
指定した行動済みユニット1体を,再び行動可能にします。	행동을 마친 유닛 하나를 다시 행동할 수 있게 합니다.
倒されたユニットを1体だけ復活します。選択可能です。	격추된 유닛 하나를 부활시킵니다. 대상을 선택할 수 있습니다.
1タ-ンの間,敵から攻撃されなくなります。<NL>また,反撃も受けません。	1턴 동안 적의 공격 대상이 되지 않습니다.<NL>또한 반격도 받지 않습니다.
指定した敵ユニットの気力を10下げます。	지정한 적 유닛의 기력을 10 낮춥니다.
自爆し,隣接したユニット(味方含む)にHP分の<NL>防御無視ダメ-ジを与えます。	자폭하여 인접한 유닛(아군 포함)에 현재 HP만큼의<NL>방어 무시 피해를 줍니다.
一回だけ指定した味方ユニットの代わりに,<NL>マップ兵器以外の敵の攻撃を受けます。	한 번만 지정한 아군 유닛을 대신하여<NL>맵 병기 이외의 적 공격을 받습니다.
1タ-ンの間,敵の命中率が半分になります。<NL>ただし「必中」は優先されます。	1턴 동안 적의 명중률이 절반이 됩니다.<NL>단, ‘필중’이 우선됩니다.
1タ-ンの間,自分の装甲が2倍になります。	1턴 동안 자신의 장갑이 2배가 됩니다.
1回だけ相手に与えるダメ-ジが3倍になります。<NL>「熱血」との併用はできません。	한 번만 상대에게 주는 피해가 3배가 됩니다.<NL>‘열혈’과 함께 사용할 수 없습니다.
次の戦闘で得られる経験値を2倍にしてくれます。	다음 전투에서 얻는 경험치가 2배가 됩니다.
指定した敵が自分を狙ってきます。	지정한 적이 자신을 노리게 합니다.
マップ上にいる好きな味方キャラクタ-の精神コマンドを,<NL>通常の倍の精神ポイント消費で使えます。	맵에 있는 원하는 아군 캐릭터의 정신기를<NL>평소의 2배 정신 포인트를 써서 사용할 수 있습니다.
相手のステ-タスを調べることができます。	상대의 상태를 확인할 수 있습니다.
つかっても効果がありません。	사용해도 효과가 없습니다.
""")


ENHANCEMENT_PARTS = parse_pairs(r"""
ブ-スタ-	부스터
メガブ-スタ-	메가 부스터
サイコフレ-ム	사이코 프레임
バイオセンサ-	바이오 센서
アポジモ-タ-	아포지 모터
マグネットコ-ティング	마그넷 코팅
Iフィ-ルド発生機	I필드 발생기
プロペラントタンク	프로펠런트 탱크
プロペラントタンクS	프로펠런트 탱크 S
ミノフスキ-クラフト	미노프스키 크래프트
高性能レ-ダ-	고성능 레이더
チョバムア-マ-	초밤 아머
ハイブリッドア-マ-	하이브리드 아머
ハロ	하로
バリアジェネレ-タ-	배리어 제너레이터
対ビ-ムコ-ティング	대빔 코팅
リペアキット	리페어 키트
超合金Z	초합금 Z
超合金ニュ-Z	초합금 뉴 Z
リロ-ダ-	리로더
リペアキットS	리페어 키트 S
バイオニックコンデンサ-	바이오닉 콘덴서
移動力+1	이동력+1
移動力+2	이동력+2
限界反応+20,運動性+12	한계 반응+20, 운동성+12
限界反応+5,運動性+8	한계 반응+5, 운동성+8
移動力+1,運動性+3	이동력+1, 운동성+3
限界反応+10,運動性+5	한계 반응+10, 운동성+5
Iフィ-ルドを装備できます。	I필드를 장비할 수 있습니다.
エネルギ-50回復。使い捨て。	에너지를 50 회복합니다. 일회용입니다.
エネルギ-200回復。使い捨て。	에너지를 200 회복합니다. 일회용입니다.
ユニットのタイプが空陸になり,地形対応が空Aになります。	유닛 타입이 공·육이 되며 지형 적응이 공 A가 됩니다.
マップ兵器,射程1の武器以外の武器の射程が1増えます。	맵 병기와 사거리 1 무기를 제외한 무기의 사거리가 1 늘어납니다.
HP+200,装甲+50	HP+200, 장갑+50
HP+300,装甲+100	HP+300, 장갑+100
限界反応+20,運動性+15,移動力+2	한계 반응+20, 운동성+15, 이동력+2
ビ-ムバリアを装備できます。	빔 배리어를 장비할 수 있습니다.
ビ-ムコ-トを装備できます。	빔 코트를 장비할 수 있습니다.
HP2000回復。使い捨て。	HP를 2000 회복합니다. 일회용입니다.
HP+200,装甲+150	HP+200, 장갑+150
HP+300,装甲+200	HP+300, 장갑+200
残弾回復。使い捨て。	잔탄을 모두 회복합니다. 일회용입니다.
HPフル回復。使い捨て。	HP를 전부 회복합니다. 일회용입니다.
EN+50	EN+50
""")


PILOT_SKILLS: dict[str, str] = {}
for _level in range(1, 10):
    PILOT_SKILLS[f"ニュ-タイプL{_level}"] = f"뉴타입 L{_level}"
    PILOT_SKILLS[f"強化人間L{_level}"] = f"강화인간 L{_level}"
    PILOT_SKILLS[f"シ-ルド防御L{_level}"] = f"실드 방어 L{_level}"
    PILOT_SKILLS[f"切り払いL{_level}"] = f"베어내기 L{_level}"
PILOT_SKILLS["底力"] = "저력"


UNIT_ABILITIES = parse_pairs(r"""
ビ-ムコ-ト	빔 코트
Iフィ-ルド	I필드
オ-ラバリア	오라 배리어
イデバリア	이데 배리어
分身	분신
ゲッタ-ビジョン	겟타 비전
マッハスペシャル	마하 스페셜
真.マッハスペシャル	진 마하 스페셜
HP回復(小)	HP 회복(소)
HP回復(大)	HP 회복(대)
暴走	폭주
ス-パ-モ-ド	슈퍼 모드
バ-サ-カ-モ-ド	버서커 모드
ATフィ-ルド	AT 필드
合体	합체
分離	분리
変形	변형
S2機関	S2 기관
ビ-ム吸収	빔 흡수
MAP兵器無効	MAP 병기 무효
遠距離攻撃無効	원거리 공격 무효
""")


SCENARIO_TITLES = parse_pairs(r"""
救出!!　Zガンダム	구출!! Z 건담
謀略の町	모략의 도시
ダカ-ルの制圧	다카르 제압
ストライク.バック	스트라이크 백
裏切りの荒野	배신의 황야
敵要塞を破壊せよ	적 요새를 파괴하라
決闘!!　ラインX1	결투!! 라인 X1
マ=クベの罠	마 쿠베의 함정
復讐の風	복수의 바람
マリオネット.フォウ	마리오네트 포우
驚異!!　究極ロボ　ヴァルシオン	경이!! 궁극 로봇 발시온
ラサの攻防	라사의 공방
シロッコの影	시로코의 그림자
宇宙へ‥‥	우주로……
激闘!　ジュピトリス	격전! 주피트리스
脱出	탈출
ソ-ラレイ発動	솔라 레이 발동
潜入!　コンペイト-	잠입! 콘페이토
月面の死闘	월면의 사투
決戦!!　グラナダ要塞	결전!! 그라나다 요새
時間よ,止まれ	시간이여, 멈춰라
大気圏突入	대기권 돌입
逆襲のシロッコ	시로코의 역습
ギルギルガンの亡霊	길길간의 망령
ラストバタリオン再び	라스트 바탈리온, 다시 한번
暁の決戦	새벽의 결전
""")


# Filled below as an explicit 411-name reviewed catalogue.  An exact table is
# preferable here to productive kana transliteration: many attacks are coined
# names whose established Korean spelling cannot be recovered mechanically.
WEAPON_NAMES = parse_pairs(r"""
----------	----------
ビ-ムサ-ベル	빔 사벨
ビ-ムライフル	빔 라이플
バルカン	발칸
ハイパ-バズ-カ	하이퍼 바주카
ハイパ-ハンマ-	하이퍼 해머
ミサイルランチャ-	미사일 런처
ロングライフル	롱 라이플
ロングビ-ムサ-ベル	롱 빔 사벨
グレネ-ドランチャ-	그레네이드 런처
ハイパ-メガランチャ-	하이퍼 메가 런처
ビ-ムガン	빔 건
ダブルバルカン	더블 발칸
ダブルビ-ムライフル	더블 빔 라이플
ダブルキャノン	더블 캐논
ハイパ-ビ-ムサ-ベル	하이퍼 빔 사벨
ハイメガキャノン	하이 메가 캐논
ビ-ムキャノン	빔 캐논
フィンファンネル	핀 판넬
メガマシンキャノン	메가 머신 캐논
ヴェスバ-	베스바
ガトリングガン	개틀링 건
アトミックバズ-カ	아토믹 바주카
メガビ-ム砲	메가 빔포
大型ビ-ムサ-ベル	대형 빔 사벨
フォ-ルディングバズ-カ	폴딩 바주카
爆導索	폭도삭
集束ミサイル	집속 미사일
マイクロミサイル	마이크로 미사일
240ミリキャノン	240mm 캐논
120ミリキャノン	120mm 캐논
ボップミサイル	보프 미사일
小型ミサイル	소형 미사일
ハンドグレネイド	핸드 그레네이드
グレネイドランチャ-	그레네이드 런처
メガビ-ムキャノン	메가 빔 캐논
ゲッタ-レザ-	겟타 레이저
ゲッタ-トマホ-ク	겟타 토마호크
トマホ-クブ-メラン	토마호크 부메랑
ゲッタ-ビ-ム	겟타 빔
ゲッタ-ア-ム	겟타 암
ゲッタ-ドリル	겟타 드릴
ドリルスト-ム	드릴 스톰
ドリルパンチ	드릴 펀치
格闘	격투
ゲッタ-ミサイル	겟타 미사일
大雪山おろし	대설산 던지기
スピンカッタ-	스핀 커터
ダブルトマホ-ク	더블 토마호크
ダブルトマホ-クブ-メラン	더블 토마호크 부메랑
シャインスパ-ク	샤인 스파크
ドリルア-ム	드릴 암
ライガ-ミサイル	라이거 미사일
チェ-ンアタック	체인 어택
ゲッタ-サイクロン	겟타 사이클론
ストロングミサイル	스트롱 미사일
冷凍ビ-ム	냉동 빔
光子力ビ-ム	광자력 빔
ロケットパンチ	로켓 펀치
アイアンカッタ-	아이언 커터
ミサイル	미사일
ドリルミサイル	드릴 미사일
ルストハリケ-ン	루스트 허리케인
ブレストファイヤ-	브레스트 파이어
スクランダ-カッタ-	스크랜더 커터
サザンクロスナイフ	서던 크로스 나이프
ニ-インパルスキック	니 임펄스 킥
バックスピンキック	백스핀 킥
マジンガ-ブレ-ド	마징가 블레이드
スクランブルカッタ-	스크램블 커터
ネ-ブルミサイル	네이블 미사일
グレ-トタイフ-ン	그레이트 타이푼
グレ-トブ-メラン	그레이트 부메랑
アトミックパンチ	아토믹 펀치
ドリルプレッシャ-パンチ	드릴 프레셔 펀치
ブレストバ-ン	브레스트 번
サンダ-ブレ-ク	썬더 브레이크
グレ-トブ-スタ-	그레이트 부스터
修理装置	수리 장치
ダイアナンミサイル	다이아난 미사일
スカ-レットビ-ム	스칼렛 빔
補給装置	보급 장치
ボロットパンチ	보로트 펀치
スペシャルボロットパンチ	스페셜 보로트 펀치
スペシャルDXボロットパンチ	스페셜 DX 보로트 펀치
光子力ミサイル	광자력 미사일
フィンガ-ミサイル	핑거 미사일
30ミリマシンガン	30mm 머신 건
スカイリッパ-	스카이 리퍼
ドスブレッシャ-	도스 브레셔
マグネレ-ザ-	마그네 레이저
ロックファィタ-	록 파이터
エレクトロダ-ト	일렉트로 다트
マグネクロ-	마그네 클로
アトミックバ-ナ-	아토믹 버너
アンカ-ナックル	앵커 너클
380ミリ戦車砲	380mm 전차포
メカニフィクサ-	메카니 픽서
爆雷	폭뢰
クラフトドリル	크래프트 드릴
ロックファイタ-	록 파이터
バトルリタ-ン	배틀 리턴
バトルガレッガ-	배틀 가렛거
超電磁クレ-ン	초전자 크레인
スプリングクラッシャ-	스프링 크러셔
超電磁ヨ-ヨ-	초전자 요요
ツインランサ-	트윈 랜서
Vレ-ザ-	V 레이저
バトルチェ-ンソ-	배틀 체인소
ビッグブラスト	빅 블래스트
ビッグブラスト.ディバイダ-	빅 블래스트 디바이더
超電磁スパ-ク	초전자 스파크
超電磁スピン	초전자 스핀
グランダッシャ-	그란대셔
ダイタ-ンザンバ-	다이탄 잔바
ダイタ-ンハンマ-	다이탄 해머
ダイタ-ンウェッブ	다이탄 웹
ビッグウェッブ	빅 웹
ダイタ-ンミサイル	다이탄 미사일
ダイタ-ンキャノン	다이탄 캐논
サンレ-ザ-	선 레이저
サンアタック	선 어택
ロケット砲	로켓포
爆弾	폭탄
ワイヤ-クロ-	와이어 클로
オ-ラソ-ド	오라 소드
オ-ラショット	오라 샷
オ-ラ斬り	오라 베기
ハイパ-オ-ラ斬り	하이퍼 오라 베기
オ-ラキャノン	오라 캐논
オ-ラビ-ムソ-ド	오라 빔 소드
オ-ラソ-ドライフル	오라 소드 라이플
オ-ラ.バルカン	오라 발칸
コスモビ-ム	코스모 빔
レッドインパルサ-	레드 임펄서
ホルスタ-ビ-ム	홀스터 빔
ゴ-サ-ベル	고 사벨
ゴ-スティック	고 스틱
スペ-スバズ-カ	스페이스 바주카
ゴ-フラッシャ-	고 플래셔
30ミリ機銃	30mm 기관총
二連装主砲	2연장 주포
メガ粒子砲	메가 입자포
ヒ-トホ-ク	히트 호크
ザクバズ-カ	자쿠 바주카
120mmマシンガン	120mm 머신 건
シュツルムファウスト	슈투름 파우스트
75ミリ機関砲	75mm 기관포
ヒ-トロッド	히트 로드
ヒ-トサ-ベル	히트 사벨
""")

WEAPON_NAMES.update(parse_pairs(r"""
拡散ビ-ム砲	확산 빔포
ジャイアントバズ	자이언트 바주카
ハイドポンプ	하이드 펌프
クロ-	클로
ワイヤ-ビ-ム砲	와이어 빔포
ビ-ムカノン	빔 캐논
魚雷	어뢰
ハンドミサイル	핸드 미사일
アイアンネイル	아이언 네일
対艦ミサイル	대함 미사일
溶解液	용해액
毒液	독액
アイアンクロ-	아이언 클로
破壊光線	파괴 광선
グラビトンウェ-ブ	그라비톤 웨이브
超破壊光線	초파괴 광선
メガグラビトンウェ-ブ	메가 그라비톤 웨이브
ビッグミサイル	빅 미사일
フラッシャ-ビ-ム	플래셔 빔
ディスカッタ-	디스커터
カロリックミサイル	칼로릭 미사일
サイフラッシュ	사이플래시
ハイファミリア	하이 패밀리어
アカシックバスタ-	아카식 버스터
コスモノヴァ	코스모 노바
グランワ-ムソ-ド	그란 웜 소드
グラビトロンカノン	그라비트론 캐논
ワ-ムスマッシャ-	웜 스매셔
ブラックホ-ルクラスタ-	블랙홀 클러스터
縮退砲	축퇴포
ディバインア-ム	디바인 암
サイコブラスタ-	사이코 블래스터
クロスソ-サ-	크로스 소서
ハイパ-ビ-ムキャノン	하이퍼 빔 캐논
クロスマッシャ-	크로스 스매셔
超振動拳	초진동권
リニアレ-ルガン	리니어 레일건
対空ミサイル	대공 미사일
レゾナンスクエイク	레조넌스 퀘이크
フレイムカッタ-	플레임 커터
パルスレ-ザ-	펄스 레이저
中性子レ-ザ-	중성자 레이저
カロリックスマッシュ	칼로릭 스매시
メギドフレイム	메기도 플레임
グングニ-ル	궁니르
ハイドロプレッシャ-	하이드로 프레셔
ケルヴィンブリザ-ド	켈빈 블리자드
ロ-ズカッタ-	로즈 커터
ドライシュトラ-ル	드라이 슈트랄
エレメンタルフュ-ジョン	엘리멘탈 퓨전
ブラスナックル	브라스 너클
イビルアイ	이블 아이
クロスシザ-ス	크로스 시저스
ホ-ミング魚雷	호밍 어뢰
円盤	원반
斧	도끼
ビッグバンウェ-ブ	빅뱅 웨이브
2連装メガ粒子砲	2연장 메가 입자포
アッザムリ-ダ-	앗잠 리더
ビット	비트
海ヘビ	바다뱀
大型ビ-ムライフル	대형 빔 라이플
ビ-ム砲	빔포
小型メガビ-ム砲	소형 메가 빔포
拡散メガ粒子砲	확산 메가 입자포
フェダ-インライフル	페다인 라이플
クロ-ア-ム	클로 암
メガ拡散ビ-ム砲	메가 확산 빔포
レフレクタ-ビット	리플렉터 비트
サイコミュ式ビ-ムソ-ド	사이코뮤식 빔 소드
クレイバズ-カ	클레이 바주카
メガバズ-カランチャ-	메가 바주카 런처
ア-ムビ-ムガン	암 빔 건
サイコミュ式メガ粒子砲	사이코뮤식 메가 입자포
ショットガン	샷건
60ミリ機関砲	60mm 기관포
パンツァ-ファウスト	판처 파우스트
チェ-ンマイン	체인 마인
110ミリバルカン	110mm 발칸
ミサイルポッド	미사일 포드
大型メガ粒子砲	대형 메가 입자포
プラズマリ-ダ-	플라즈마 리더
110ミリ機関砲	110mm 기관포
ビ-ムマシンガン	빔 머신 건
偏向メガ粒子砲	편향 메가 입자포
有線クロ-ア-ム	유선 클로 암
大型ミサイルランチャ-	대형 미사일 런처
小型ミサイルランチャ-	소형 미사일 런처
メガカノン砲	메가 캐논포
ファンネル	판넬
フィンガ-ランチャ-	핑거 런처
エネルギ-ガン	에너지 건
有線式ビ-ム砲	유선식 빔포
ビ-ムトマホ-ク	빔 토마호크
ハンドガン	핸드건
トライブレ-ド	트라이 블레이드
ハンドビ-ム	핸드 빔
インコム	인컴
メガランチャ-	메가 런처
ビ-ムランチャ-	빔 런처
メガビ-ムカノン	메가 빔 캐논
ハイパ-メガ粒子砲	하이퍼 메가 입자포
ビ-ムソ-ドアックス	빔 소드 액스
メガガトリングガン	메가 개틀링 건
ビ-ムアサルトライフル	빔 어설트 라이플
ビ-ムショットライフル	빔 샷 라이플
有線式メガア-ム砲	유선식 메가 암포
ショットランサ-	샷 랜서
テンタクラ-ロッド	텐타클 로드
バグ	버그
ミニミサイル	미니 미사일
マグマ弾	마그마탄
体当たり	몸통박치기
しっぽ	꼬리
火炎	화염
ハンドソルド	핸드 소드
ロケット弾	로켓탄
大砲	대포
レ-ザ-	레이저
かま	낫
ブ-メラン	부메랑
ムチ	채찍
ピストル	권총
ライフル	라이플
スピア	스피어
ハリケ-ン	허리케인
磁力光線	자력 광선
ショックビ-ム	쇼크 빔
うずまき	소용돌이
リング光線	링 광선
ドリル	드릴
振動波	진동파
くちばし	부리
ライトニングアロ-	라이트닝 애로
ビッグレ-ザ-	빅 레이저
触手	촉수
超音波	초음파
マリンヴォルテックス	마린 볼텍스
フレイボム	플레임 봄
オ-ラバルカン	오라 발칸
オ-ラショットア-ム	오라 샷 암
ハイパ-オ-ラショットア-ム	하이퍼 오라 샷 암
ハイパ-オ-ラキャノン	하이퍼 오라 캐논
ウイングソ-ド	윙 소드
ビッグボウ	빅 보우
60ミリ機銃	60mm 기관총
ホ-ミングミサイル	호밍 미사일
ラム	램
サ-ベル	사벨
バズ-カ	바주카
"""))

WEAPON_NAMES.update(parse_pairs(r"""
メインメガ粒子砲	메인 메가 입자포
サブメガ粒子砲	서브 메가 입자포
120ミリ機関砲	120mm 기관포
対空機関砲	대공 기관포
12連装ミサイルランチャ-	12연장 미사일 런처
15連装ミサイルランチャ-	15연장 미사일 런처
90ミリ機関砲	90mm 기관포
140ミリ機関砲	140mm 기관포
20連装ミサイルランチャ-	20연장 미사일 런처
ハイメガ粒子砲	하이 메가 입자포
160ミリ機関砲	160mm 기관포
180ミリ機関砲	180mm 기관포
レ-ザ-光線	레이저 광선
大型ミサイル	대형 미사일
冷凍光線	냉동 광선
高圧電流	고압 전류
未使用	미사용
蝶の舞	나비의 춤
スクリュ-クラッシャ-パンチ	스크류 크러셔 펀치
反重力スト-ム	반중력 스톰
ショルダ-ブ-メラン	숄더 부메랑
スペ-スサンダ-	스페이스 썬더
ダブルハ-ケン	더블 하켄
ダブルショルダ-ブ-メラン	더블 숄더 부메랑
スピンソ-サ-	스핀 소서
ドリルソ-サ-	드릴 소서
メルトシャワ-	멜트 샤워
スピンドリル	스핀 드릴
ダブルカッタ-	더블 커터
サイクロンビ-ム	사이클론 빔
ダブルミサイル	더블 미사일
マリンミサイル	마린 미사일
マリンカッタ-	마린 커터
マリンビ-ム	마린 빔
スパ-クボンバ-	스파크 봄버
ドリルアタック	드릴 어택
機銃	기관총
ビ-ムスプレ-ガン	빔 스프레이 건
ビ-ムナギナタ	빔 나기나타
ビ-ムピストル	빔 피스톨
ハンドグレネ-ド	핸드 그레네이드
多弾頭ミサイル	다탄두 미사일
レ-ザ-ビ-ム	레이저 빔
ソニックブラスト	소닉 블래스트
テキサスソ-ド	텍사스 소드
マックリボルバ-	맥 리볼버
マックライアット	맥 라이엇
マックライフル	맥 라이플
ダ-クサ-ベル	다크 사벨
110ミリマシンガン	110mm 머신 건
パンチ	펀치
トマホ-ク	토마호크
ゴッドブレイカ-	갓 브레이커
ゴッドミサイル	갓 미사일
ゴッドブ-メラン	갓 부메랑
ゴ-ガンソ-ド	고간 소드
エネルギ-カッタ-	에너지 커터
ゴッドアルファ	갓 알파
ゴッドプレッシャ-	갓 프레셔
ゴッドアロ-	갓 애로
ゴッドゴ-ガン	갓 고간
ゴッドバ-ド	갓 버드
ゴッドボイス	갓 보이스
ゴッドサンダ-	갓 썬더
ゴッドバ-ド二段がえし	갓 버드 2단 되치기
20ミリバルカン	20mm 발칸
機雷	기뢰
セラミックソ-ド	세라믹 소드
ギルソ-ド	길 소드
ギルアロ-	길 애로
妖魔サ-べル	요마 사벨
妖魔光線	요마 광선
ヘアミサイル	헤어 미사일
ガンテミサイル	간테 미사일
リッパ-クロ-	리퍼 클로
電磁もり	전자 작살
アロ-ランサ-	애로 랜서
メガフラッシャ-	메가 플래셔
高周波ソ-ド	고주파 소드
メガスマッシャ-	메가 스매셔
メガビ-ムサ-ベル	메가 빔 사벨
メガビ-ムライフル	메가 빔 라이플
サンダ-クラッシュ	썬더 크래시
フォトンビ-ム砲	포톤 빔포
高周波ブレ-ド	고주파 블레이드
ボルテックシュ-タ-	볼테크 슈터
ハンマ-	해머
バニッシュレ-ザ-	배니시 레이저
拡散バズ-カ	확산 바주카
オ-ラノバ砲	오라 노바포
クラフトミサイル	크래프트 미사일
大車輪ロケットパンチ	대차륜 로켓 펀치
105ミリバルカン	105mm 발칸
クロ-ミサイル	클로 미사일
対空メガ粒子砲	대공 메가 입자포
2連装機銃砲	2연장 기관포
2連装ビ-ム砲	2연장 빔포
誘導ミサイル	유도 미사일
ビ-ムソ-ド	빔 소드
隠し腕	숨겨진 팔
30ミリバルカン	30mm 발칸
ハイド.ボンブ	하이드 봄
4連マシンキャノン	4연장 머신 캐논
ダブルビ-ムガン	더블 빔 건
イビルリング	이블 링
ナックルバスタ-	너클 버스터
2連装ミサイルランチャ-	2연장 미사일 런처
9連装ミサイルランチャ-	9연장 미사일 런처
ハイパ-ナックルバスタ-	하이퍼 너클 버스터
大型ビ-ムマシンガン	대형 빔 머신 건
海ヘビ(MAP兵器)	바다뱀(MAP 병기)
"""))


CATALOGUES: dict[str, dict[str, str]] = {
    "terrain_combinations": TERRAIN_COMBINATIONS,
    "terrain_names": TERRAIN_NAMES,
    "spirit_commands": SPIRIT_COMMANDS,
    "enhancement_parts": ENHANCEMENT_PARTS,
    "weapon_names": WEAPON_NAMES,
    "pilot_skills": PILOT_SKILLS,
    "unit_abilities": UNIT_ABILITIES,
    "scenario_titles": SCENARIO_TITLES,
}


REVIEW_NOTES = {
    "⟦G:70C⟧裂": "미매핑 첫 글리프는 문맥과 지형 목록을 근거로 ‘균’에 해당하는 균열 표기로 복원함.",
    "⟦G:562⟧": "정신기 순서와 설명을 근거로 미매핑 글리프를 ‘魂(혼)’으로 복원함.",
}


def normalized_glossary_key(text: str) -> str:
    # The embedded UI font writes the long-vowel mark as ASCII '-'.
    return text.replace("-", "ー")


def load_approved_glossary(document: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for category in ("speaker_names", "katakana_terms", "kanji_compounds"):
        for row in document[category]:
            if row.get("status") != "approved" or not row.get("ko_approved"):
                continue
            previous = result.setdefault(row["ja"], row["ko_approved"])
            if previous != row["ko_approved"]:
                raise ValueError(f"conflicting approved glossary entry: {row['ja']}")
    return result


def load_width_compaction() -> dict[str, dict[str, str]]:
    """Keep reviewed display-width overrides across catalogue regeneration."""

    if not OUTPUT.exists():
        return {}
    existing = json.loads(OUTPUT.read_text(encoding="utf-8"))
    value = existing.get("width_compaction", {})
    if not isinstance(value, dict):
        raise ValueError("existing width_compaction must be an object")
    return value


def translation_source(
    asset_id: str, source_text: str, korean_text: str, approved: dict[str, str]
) -> str:
    if source_text == "----------":
        return "preserved_nontext_placeholder"
    approved_text = approved.get(normalized_glossary_key(source_text))
    if approved_text is not None:
        if approved_text != korean_text:
            raise ValueError(
                f"approved glossary conflict for {source_text!r}: "
                f"catalogue={korean_text!r}, approved={approved_text!r}"
            )
        return "approved_glossary"
    if asset_id in {"spirit_commands", "enhancement_parts"} and len(source_text) > 20:
        return "manual_semantic_translation"
    return "reviewed_srw_terminology"


def build_document() -> dict[str, Any]:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    glossary_document = json.loads(GLOSSARY.read_text(encoding="utf-8"))
    approved = load_approved_glossary(glossary_document)
    width_compaction = load_width_compaction()

    source_tables = {row["asset_id"]: row for row in inventory["other_menu_visible_tables"]}
    if set(TARGET_TABLES) - source_tables.keys():
        raise ValueError("inventory is missing a requested UI table")

    tables: list[dict[str, Any]] = []
    source_counts: Counter[str] = Counter()
    global_korean_by_source: dict[str, str] = {}
    source_occurrences: defaultdict[str, int] = defaultdict(int)

    for asset_id in TARGET_TABLES:
        source_table = source_tables[asset_id]
        target_records = [row for row in source_table["records"] if row["translation_target"]]
        expected = EXPECTED_TARGET_COUNTS[asset_id]
        if len(target_records) != expected or source_table["translation_target_count"] != expected:
            raise ValueError(
                f"{asset_id}: expected {expected} target records, got {len(target_records)}"
            )

        catalogue = CATALOGUES[asset_id]
        unique_sources = {row["japanese_text"] for row in target_records}
        missing = sorted(unique_sources - catalogue.keys())
        extra = sorted(catalogue.keys() - unique_sources)
        if missing or extra:
            raise ValueError(f"{asset_id}: catalogue mismatch missing={missing!r} extra={extra!r}")

        entries: list[dict[str, Any]] = []
        for row in target_records:
            source_text = row["japanese_text"]
            korean_text = catalogue[source_text]
            if not korean_text:
                raise ValueError(f"{asset_id}[{row['entry_index']}]: empty Korean text")
            if JAPANESE_RE.search(korean_text) or UNRESOLVED_GLYPH_RE.search(korean_text):
                raise ValueError(
                    f"{asset_id}[{row['entry_index']}]: Japanese/unresolved glyph remains: "
                    f"{korean_text!r}"
                )
            if source_text.count("\n") != korean_text.count("\n"):
                raise ValueError(
                    f"{asset_id}[{row['entry_index']}]: source/Korean line-break count differs"
                )
            previous = global_korean_by_source.setdefault(source_text, korean_text)
            if previous != korean_text:
                raise ValueError(
                    f"duplicate source has inconsistent Korean: {source_text!r}: "
                    f"{previous!r} vs {korean_text!r}"
                )

            source_kind = translation_source(asset_id, source_text, korean_text, approved)
            source_counts[source_kind] += 1
            source_occurrences[source_text] += 1
            entries.append(
                {
                    "index": row["entry_index"],
                    "pointer_field": row["pointer_field"],
                    "pointer_field_hex": row["pointer_field_hex"],
                    "source_offset": row["source_offset"],
                    "source_offset_hex": row["source_offset_hex"],
                    "raw_sha256": row["raw_sha256"],
                    "source_text": source_text,
                    "korean_text": korean_text,
                    "status": (
                        "preserved_nontext" if source_kind == "preserved_nontext_placeholder" else "translated"
                    ),
                    "control_signature": row["control_signature"],
                    "review": {
                        "status": "reviewed",
                        "translation_source": source_kind,
                        "notes": REVIEW_NOTES.get(source_text),
                    },
                }
            )

        tables.append(
            {
                "asset_id": asset_id,
                "header_offset": source_table["header_offset"],
                "header_offset_hex": f"0x{source_table['header_offset']:X}",
                "pointer_start": source_table["table_start"],
                "pointer_start_hex": f"0x{source_table['table_start']:X}",
                "count": source_table["entry_count"],
                "target_count": len(entries),
                "unique_source_count": len(unique_sources),
                "entries": entries,
            }
        )

    target_count = sum(table["target_count"] for table in tables)
    expected_total = sum(EXPECTED_TARGET_COUNTS.values())
    if target_count != expected_total:
        raise ValueError(f"total coverage mismatch: {target_count}/{expected_total}")

    duplicate_occurrence_count = sum(count - 1 for count in source_occurrences.values())
    return {
        "schema": "srwcb-second-ui-tables-overlay-v1",
        "purpose": "Reviewed Korean text for SECOND.WAR menu/status tables; no binary modifications",
        "source": {
            "inventory_path": INVENTORY.relative_to(ROOT).as_posix(),
            "inventory_sha256": sha256_file(INVENTORY),
            "executable_path": inventory["source"]["path"],
            "executable_sha256": inventory["source"]["sha256"],
            "approved_glossary_path": GLOSSARY.relative_to(ROOT).as_posix(),
            "approved_glossary_sha256": sha256_file(GLOSSARY),
            "approved_glossary_version": glossary_document["approval"]["version"],
            "dialogue_overlay_path": DIALOGUE_OVERLAY.relative_to(ROOT).as_posix(),
            "dialogue_overlay_sha256": sha256_file(DIALOGUE_OVERLAY),
        },
        "policy": {
            "priority": [
                "approved_glossary",
                "reviewed_srw_terminology",
                "manual_semantic_translation",
            ],
            "duplicate_source_policy": "identical source_text always has identical korean_text",
            "placeholder_policy": "nontext hyphen placeholders are preserved exactly",
            "control_policy": "source control_signature and explicit line-break count are preserved",
            "binary_edit_policy": "overlay only; executable and disc images are not modified",
        },
        "width_compaction": width_compaction,
        "statistics": {
            "table_count": len(tables),
            "target_count": target_count,
            "expected_target_count": expected_total,
            "coverage": f"{target_count}/{expected_total}",
            "table_scoped_unique_source_count": sum(
                table["unique_source_count"] for table in tables
            ),
            "global_unique_source_count": len(source_occurrences),
            "duplicate_occurrence_count": duplicate_occurrence_count,
            "empty_korean_count": 0,
            "japanese_remnant_count": 0,
            "translation_source_counts": dict(sorted(source_counts.items())),
        },
        "tables": tables,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate catalogues and require the existing output to match regenerated JSON",
    )
    args = parser.parse_args()

    document = build_document()
    encoded = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if args.check_only:
        if not OUTPUT.exists():
            raise SystemExit(f"missing output: {OUTPUT}")
        if OUTPUT.read_bytes() != encoded:
            raise SystemExit(f"stale output: regenerate {OUTPUT}")
        print(json.dumps(document["statistics"], ensure_ascii=False, indent=2))
        return 0

    OUTPUT.write_bytes(encoded)
    print(f"wrote {OUTPUT}")
    print(json.dumps(document["statistics"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
