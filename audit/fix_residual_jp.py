# -*- coding: utf-8 -*-
"""원문 바이트가 그대로 남은 자리를 **제자리에서** 한글로 바꾼다.

폰트를 한글로 갈아 끼웠기 때문에, 번역이 안 된 레코드는 화면에서 일본어가
아니라 **뜻 모를 한글**로 나온다(예: 分岐 선택지 `早乙女研究所/光子力研究所`
가 '건걷걸근귿글'). 감사기는 한글맵으로 읽어 한글이 보이면 통과시켜 왔기
때문에 이런 자리를 오래 놓쳤다(2026-08-10 제보 #8).

여기서는 **길이를 늘리지 않는** 치환만 한다. 짧아지는 만큼은 빈칸(0x00)으로
메워 레코드 길이·포인터·작전목적 블록의 줄 수가 전부 그대로 남는다.
"""
import os
import sys

_d = os.path.dirname(os.path.abspath(__file__))
while _d != os.path.dirname(_d) and not os.path.exists(os.path.join(_d, "srwcb_paths.py")):
    _d = os.path.dirname(_d)
for _s in ("", "tools"):
    _p = os.path.join(_d, _s) if _s else _d
    if _p not in sys.path:
        sys.path.insert(0, _p)

import srwcb_paths as _P                       # noqa: E402
import second_translation_codec as C           # noqa: E402
import json                                    # noqa: E402

_REVIEWED = json.loads(_P.FONT_MAPPING.read_text(encoding="utf-8")) \
    if str(_P.FONT_MAPPING).endswith(".json") else None


def _jp_index():
    """원문 글자 -> 글리프 번호 (레트일 폰트)."""
    rows = json.loads((_P.WORK / "research" /
                       "srwcb_embedded_font_mapping_reviewed.json").read_text(encoding="utf-8"))["rows"]
    out = {}
    for r in rows:
        ch = r.get("character")
        if ch and ch not in out:
            out[ch] = r["glyph_index"]
    return out


def _enc(text, table):
    return b"".join(C.encode_glyph_index(table[ch]) for ch in text)


# 파일 -> [(원문, 한국어), …]  길이가 늘면 실패시킨다.
_CONDITIONS = [
    ("フェイルロ-ド(デュラクシ-ル)を倒す事", "페일로드(듀락실) 격파"),
    ("ヴォルクルスを倒す事", "볼쿠르스격파"),
    ("移動要塞を倒す事", "이동 요새 격파"),
    ("敵のせん滅", "적 섬멸"),
    ("敵の全滅", "적 전멸"),
    ("味方の全滅", "아군 전멸"),
]

# 유닛 능력 화면 `특수 능력  실드○` 의 값 표(2칸: 無 / 有).
# 제2차·EX·TR·SLPS 는 번역돼 있었는데 **제3차만 빠져** 깨진 글자가 나왔다
# (2026-08-10 제보 #7). 표기도 갈려 있어(제2차·SLPS '끔/켬') 무/유로 통일한다.
_SHIELD = [("無有", "무유")]

REPLACEMENTS = {
    "SECOND/2_SCE.BIN": _CONDITIONS,
    "THIRD/3_SCE.BIN": _CONDITIONS,
    "EX/E_SCE.BIN": [
        # 작전목적(승리조건). 시나리오 헤더 레코드 안이라 길이를 못 바꾼다.
        ("フェイルロ-ド(デュラクシ-ル)を倒す事", "페일로드(듀락실) 격파"),
        ("ヴォルクルスを倒す事", "볼쿠르스격파"),
        ("移動要塞を倒す事", "이동 요새 격파"),
        ("敵のせん滅", "적 섬멸"),
        ("敵の全滅", "적 전멸"),
        # 시나리오 11(코럴 캐니언 다시) 1단계 승리조건. 레코드 0(이벤트 스크립트)
        # 꼬리라 원장 번역 대상이 아니었고, 가나는 폰트 교체 뒤에도 가나로 남고
        # 한자 2글자만 뜻 모를 한글로 떠 있었다(제보 #11 동반). 8바이트 = 8바이트.
        ("リィナの救出", "리나구출"),
    ],
    "SECOND/SECOND.WAR": _SHIELD,
    "EX/EX.WAR": _SHIELD,
    # 트레이닝 모드 종료 확인문. 라벨 힙에 있는데 번역이 안 들어가 한자 두 글자
    # 자리가 뜻 모를 한글('꽂꽃')로 렌더됐다. 19바이트 자리, 18바이트로 들어간다.
    "TR.WAR": _SHIELD + [("トレ-ニングモ-ドを終了しますか?", "훈련을 종료할까요?")],
    "SLPS_020.70": _SHIELD + [("トレ-ニングモ-ドを終了しますか?", "훈련을 종료할까요?")],
    "THIRD/THIRD.WAR": [
        ("無有", "무유"),
        ("セ-ブ終了しました。ゲ-ムを続けますか?", "저장 완료. 계속할까요?"),
        ("セ-ブします。よろしいですか?", "저장할까요?"),
        ("ボ-ナス経験値", "보너스 EXP"),
        # '×' 는 한글 폰트에 없다 — 뒤의 '×2' 는 원문 그대로 둔다.
        ("残りの精神ポイント", "남은 정신 P"),
        ("味方の全滅", "아군 전멸"),
    ],
}


# 이미 한글인데 문구/자리를 손봐야 하는 것 — (현재 한국어, 바꿀 한국어).
# 길이가 늘면 실패시키고, 짧아지면 빈칸으로 메운다.
KO_FIXUPS = {
    # 제2차·SLPS 는 '끔/켬'(켜고 끄기)으로 돼 있었다. 실드는 켜고 끄는 게
    # 아니라 있고 없고다 — 원문도 無/有 다.
    "SECOND/SECOND.WAR": [("끔켬", "무유")],
    "SLPS_020.70": [("끔켬", "무유")],
    # ※ 출격 머리글(`출격유닛선택남은` -> `출격유닛남음`)은 여기 있었는데
    #   **셈이 한 칸 틀렸다** — 8전각(12칸)을 6전각(9칸)+반각4(4칸)=13칸으로
    #   바꿔 한 칸이 늘었고, 뒤따르는 `10기`·`기력 100`·`LV순` 이 통째로 8px
    #   오른쪽으로 밀렸다(제보 #18b). 게다가 제2차·SLPS 는 `선택` 뒤에 빈칸이
    #   있어 이 패턴이 아예 안 맞았는데도 조용히 넘어갔다(제보 #9).
    #   지금은 아래 `_SORTIE_HEADER` 가 세 형태를 전부 같은 결과로 맞춘다.
}

# 세이브/로드 화면 머리글의 앵커 보정 (제보 #6).
#
#   레트일:  … [FC 07 00] [F8 00]=`セ-ブデ-タ`(6칸) [FC 09 00] [F8 00]=`スロット1`
#
# 라벨 자리는 6바이트 고정이라 한국어로는 6칸을 못 만든다. 전각 글리프는
# `1+phase` 칸씩 나아가므로 (전각2+반각2) = 5칸이 한계다. 한 칸 모자란 만큼을
# **바로 뒤 커서 이동에서 되돌려** 슬롯 라벨을 레트일과 같은 x 에 앉힌다.
#   `저장`+반각2 = 5칸(phase 그대로) + 이동 10칸 = 15칸 = 레트일(6+9)과 동일.
#
# 라벨을 phase 중립으로 바꾼 것(`세이브`(전각3, phase 뒤집힘) -> `저장`)이
# 목록 첫 줄 `자료01` 만 반 칸 밀려 보이던 것도 함께 없앤다 — phase 는 F6(줄
# 바꿈)에서만 초기화돼서, 머리글이 뒤집어 놓은 phase 가 첫 줄까지 새어 나갔다.
_SAVE_HEADER_ANCHOR = [("fc 07 00 f8 00 fc 09 00 f8 00",
                        "fc 07 00 f8 00 fc 0a 00 f8 00")]

# 출격 유닛 선택 목록의 파일럿명·LV 칸을 한 칸 왼쪽으로 (제보 #7).
#
#   [F7]<창> [FB ff ff][F8 01]=유닛명 [FC 13 fe] [FB ff ff][F8 01]=파일럿명
#   [FC 08 fe] [F8 00]=LV [FC 01 00] [F8 83]=레벨
#
# `FC dx dy` 는 **이름 칸 시작 기준** 상대 이동이라 열 위치가 이 값으로 정해진다.
# 레트일이 19칸(0x13)이라 레벨 숫자가 x=294 에서 시작해 두 자리면 상자 오른쪽
# 테두리(≈305)를 넘는다. 18칸(0x12)으로 당기면 286 에서 시작해 302 에서 끝나
# 상자 안에 들어오고, 같은 목록을 쓰는 아군부대표 화면(0x11 + 창 원점 차이)과
# 파일럿·LV·숫자 열이 정확히 같은 자리가 된다.
#
# ★ 행 끝의 `FC dc 02`(dx -36)는 **다음 줄 시작으로 돌아가는** 이동이다.
#   한 줄의 dx 합(레트일 출격 -8 / 부대표 -10)을 그대로 둬야 줄마다 같은 x
#   에서 시작한다. 열 이동만 줄이면 한 줄에 한 칸씩 **계단처럼 밀린다**
#   (v0.11.31 에서 실제로 그랬다). 줄인 만큼 복귀 이동에서 되돌린다.
_SORTIE_ROW = [("f7 00 40 fb ff ff f8 01 fc 13 fe fb ff ff f8 01 fc 08 fe"
                " f8 00 fc 01 00 f8 83 fc dc 02",
                "f7 00 40 fb ff ff f8 01 fc 12 fe fb ff ff f8 01 fc 08 fe"
                " f8 00 fc 01 00 f8 83 fc dd 02")]

# 아군부대표 목록의 **유닛명만** 한 칸 왼쪽으로 (제보 #7).
#
# 목록 창을 열기 직전 커서 이동이 출격 화면은 `FC 01 ff`(+1), 부대표는
# `FC 02 ff`(+2) 다. 이 한 칸 때문에 부대표만 유닛명이 들여써져 보인다
# (출격 x=39 / 부대표 x=47, 아이콘은 두 화면 다 24 근처).
# +1 로 당기고, 뒤따르는 열 이동을 0x11 -> 0x12 로 되돌려 **파일럿·LV·레벨은
# 있던 자리 그대로** 둔다.
#
# ★ 이 창 열기 이동도 **행 루프 안**이다. -1 과 +1 이 이미 서로 상쇄되므로
#   복귀 이동(`FC dc 02`)은 **그대로 둬야** 한다. 출격 쪽처럼 복귀까지 건드리면
#   한 줄에 -1 씩 더해져 줄마다 밀린다(v0.11.32 에서 실제로 그랬다).
#   루프 전체 dx 합: 레트일 1+19+8+1-36 = -7(출격) / 2+17+8+1-36 = -8(부대표).
_ROSTER_ROW = [("fc 02 ff f7 00 40 fb ff ff f8 01 fc 11 fe fb ff ff f8 01 fc 08 fe"
                " f8 00 fc 01 00 f8 83 fc dc 02",
                "fc 01 ff f7 00 40 fb ff ff f8 01 fc 12 fe fb ff ff f8 01 fc 08 fe"
                " f8 00 fc 01 00 f8 83 fc dc 02")]

# 출격 유닛 선택 화면 머리글 — 문구를 `출격유닛남음` 으로 통일하고 레트일과
# 같은 **14칸 / phase 0** 으로 되돌린다 (제보 #9, #18b).
#
#   레트일:  `00` 出撃(전각2) ユニット(반각4) 選択(전각2) `00` あと(반각2) = 14칸
#
# 지금은 실행파일마다 세 형태로 갈려 있다.
#   제2차·SLPS  `00 출격유닛선택 00 남은`        14칸 ✓ (문구만 옛것)
#   EX·트레이닝 `00 출격유닛선택남은 00`         14칸 ✓ (문구만 옛것, 붙어 보임)
#   제3차       `00 출격유닛남음 00×5`           **15칸 ✗** (KO_FIXUPS 의 셈 착오)
#
# 전각↔반각 교환은 칸 수를 딱 맞추기 어렵다. 전각 빈칸 `3FF`(EE FF)는 phase 0
# 에서 1칸·phase 1 에서 2칸 나아가므로 **두 개를 이어 붙이면 정확히 3칸**이고
# phase 도 제자리로 돌아온다. 그래서 세 형태 모두 같은 18바이트 결과로 맞춘다.
#   `00` + 6전각(9칸) + 3FF(1칸) + 3FF(2칸) + `00`(1칸) = 14칸, phase 0
_SORTIE_HEADER = [
    # 제3차 (KO_FIXUPS 를 뺐으므로 다음 빌드부터는 아래 EX 형태로 나오지만,
    #        옛 산출물에 다시 돌릴 때를 위해 남겨 둔다)
    ("f8 00 00 f3 3c ec 3d f1 ef ed ae ed 2c f1 fd 00 00 00 00 00 f8 82",
     "f8 00 00 f3 3c ec 3d f1 ef ed ae ed 2c f1 fd ee ff ee ff 00 f8 82"),
    # EX·트레이닝·제3차: `출격유닛선택남은`(8전각)
    ("f8 00 00 f3 3c ec 3d f1 ef ed ae f0 79 f3 d9 ed 2c f1 fa 00 f8 82",
     "f8 00 00 f3 3c ec 3d f1 ef ed ae ed 2c f1 fd ee ff ee ff 00 f8 82"),
    # 제2차·SLPS: `출격유닛선택 남은`
    ("f8 00 00 f3 3c ec 3d f1 ef ed ae f0 79 f3 d9 00 ed 2c f1 fa f8 82",
     "f8 00 00 f3 3c ec 3d f1 ef ed ae ed 2c f1 fd ee ff ee ff 00 f8 82"),
]

# 유닛 능력 화면 상단 세 칸 `유닛능력 / 파일럿능력 / 무기성능` (제보 #16).
#
# 칸 시작을 0 / 8 / 17 칸에 맞춘 것까지는 맞았는데 **phase 를 안 맞췄다**.
# `파일럿능력` 은 전각 5개라 phase 를 뒤집어 놓고, 반각 빈칸은 phase 를 못
# 되돌린다. 그래서 `무기성능` 이 17칸 + 반 칸 = x 140px 에서 시작해(레트일 136)
# 오른쪽 테두리를 2px 침범했다. 한 칸(8px)이 아니라 **반 칸(4px)** 문제다.
# 반각 두 칸을 전각 빈칸 하나로 바꾸면 폭은 그대로 2칸, phase 만 1 -> 0 이 된다.
_UNIT_TABS = [("ee a7 ed a1 ee b4 00 00 ef 59",
               "ee a7 ed a1 ee b4 ee ff ef 59")]

# 개조 확인 메시지 (제보 #9, #17a, #22, #25).
#
# 이 레코드는 실행파일의 **282엔트리 UI 문자열표**(필드상대 s32, 4바이트 비정렬)가
# 안쪽을 일곱 군데 겨눈다. 엔진은 `[ptr[n], ptr[n+1])` 구간을 한 런으로 그린다.
#
#   idx21 `HP␠` idx22 `EN␠` idx23 `운동성␠` idx24 `장갑␠` idx25 `한계반응␠`
#   idx26 = **두 숫자 사이 자리**  idx27 = 숫자2 뒤 나머지
#
# ★ **idx26 자리는 정확히 2바이트다.** 레트일은 반각 두 개(`が` `,`), v0.11.33 은
#   전각 하나(`이`) — 둘 다 글리프 경계라 문제가 없었다. v0.11.34 가 여기에
#   **반각 하나(`-`, 1바이트)** 를 넣는 바람에 idx27 이 다음 전각 글리프
#   `f1 f8`(으)의 **둘째 바이트 0xF8** 을 겨눴고, 0xF8 은 텍스트 VM 의 '치환'
#   옵코드라 폭 0x6E 로 폭주해 **유닛 개조 확정 순간 게임이 멈췄다**(제보 #22).
#   무기 개조는 다른 레코드를 써서 멀쩡했다.
#
# 그래서 반각 **두 개**(`-` + 빈칸)로 채운다 — 레트일과 같은 2칸·phase 불변이고
# 제보 #23 의 '숫자 사이에 간격이 있으면 좋겠다' 도 함께 만족한다.
#   결과: 「한계반응 240- 250으로 됨.」 / 「확인?」
_UPGRADE_MSG = [("f2 0c 3a ec 01 00 ee 00 3b f6 f4 de f2 0e 14 00 00",
                 "11 00 f1 f8 ee c0 00 ee 00 3b f6 f4 de f2 0e 14 00"),
                # v0.11.34 가 심어 놓은 1바이트짜리도 되돌린다(핫패치본 대비)
                ("11 f1 f8 ee c0 00 ee 00 3b f6 f4 de f2 0e 14 00 00",
                 "11 00 f1 f8 ee c0 00 ee 00 3b f6 f4 de f2 0e 14 00")]

# SLPS_020.70 의 같은 레코드는 아직 원문 그대로다(44바이트 통째 교체).
# 한자 자리가 뜻 모를 한글로 렌더되고 있었다 — 감사기 사각지대였다.
_UPGRADE_MSG_JP = [(
    "1d 25 00 1a 23 00 ed a6 ec 51 ed 01 00 ec 62 ec 01 00 ec 63 ec 64 ec 65 ec 66 00"
    " 4b 3a 6a 69 89 7d 58 e4 f6 87 8c 56 43 66 58 4a 14",
    "1d 25 00 1a 23 00 f1 d0 ed f5 f0 82 00 f2 25 ec 09 00 f4 a3 ec 48 ef 8b f5 38 00"
    " 11 00 f1 f8 ee c0 00 ee 00 3b f6 f4 de f2 0e 14 00")]


# 옵션 > 버튼 설정 값 표 (4바이트 고정 스트라이드, 45바이트).
#
# EX.WAR·TR.WAR 만 주입기 표 목록에서 빠져 **레트일 그대로**였다. ○✕△□ 는
# 레트일 글리프 0x8F8~0x8FB 를 가리키는데 그 슬롯이 한글로 덮여 화면에
# `톄톈토톡` 으로 떴다(2026-08-20 제보 #23·#25). 제2차 번역본을 바이트 그대로
# 가져온다 — 항목 스트라이드가 같아 자리가 하나도 안 움직인다.
_BUTTON_ROW = [(
    "69 56 00 00 f3 f8 00 00 f3 f9 00 00 f3 fa 00 00 f3 fb 00 00"
    " 21 31 00 00 27 31 00 00 21 32 00 00 27 32 00 00"
    " a8 ae 11 b7 aa db 9e b7 ff",
    "f1 7c f1 fd f5 36 00 00 f5 2f 00 00 f5 35 00 00 f5 34 00 00"
    " 21 31 00 00 27 31 00 00 21 32 00 00 27 32 00 00"
    " f0 e9 f2 1b f0 79 f3 d9 ff")]

# EX 작전목적(승리조건) 잔여 미번역 10곳 (2026-08-20 제보 #23).
#
# 이 문구들은 시나리오 헤더 레코드(이벤트 스크립트) 안이라 **원장 번역 대상이
# 아니다**. 길이를 바이트 단위로 정확히 맞추고 남는 만큼 0x00(빈칸)으로 메운다.
# REPLACEMENTS 가 먼저 도므로 일부는 **치환 뒤 바이트**를 패턴으로 쓴다.
_EX_OBJECTIVES = [
    ("bf c4 98 11 cf 3c a7 d6 9f 3d 6d ec 1c ec b2",
     "ef 87 f4 6f 3c f2 54 ec 95 3d 00 f4 38 ec 69"),                       # 바폼(조그) 파괴
    ("b6 d1 df a3 11 db cf 8e ec e9 ec ee 54 5a 8a ec a6",
     "ed db ef 43 ec 51 ee af 00 f2 3c ef 3a 00 00 00 00"),                 # 데몬골렘 전멸
    ("35 ae 11 df ed 0d ed 89 6a ec 93 8e ec e9 ec ee 54 5a 8a ec a6",
     "35 f3 e5 00 f2 0c ed 35 00 f2 3b 00 f2 3c ef 3a 00 00 00 00 00"),     # 5턴 이내 적 전멸
    ("9a 11 9e a8 ec a4 ec a5 8e ee 0b 58 ec a6",
     "f3 63 f3 bf f0 e0 00 ec 3d f4 38 00 00 00"),                          # 카크스 격파
    ("b8 db 93 9e 3c 95 92 da 95 92 c6 a8 3d 8e ee 0b 58 ec a6",
     "ee 1b ee ab f2 0c f3 bf 00 ec 3d f4 38 00 00 00 00 00 00"),           # 드레이크 격파
    ("f2 3b 00 f2 3c ef 3a 3a f5 26 6e 38 ae 11 df ee 25 60 52 5e 47 8a ec a6",
     "f2 3b 00 f2 3c ef 3a 00 ee 5b ed 9a 00 38 f3 e5 00 ef 94 f1 72 00 00 00"),
    ("a6 d4 95 3c 9f d8 df ad df 3d 8e ee 0b 58 ec a6",
     "f0 da f1 ce 3c ec 95 ee 8f f2 56 3d ec 3d f4 38"),                    # 슈우(그랑존) 격파
    ("31 30 ae 11 df ed 0d ed 89 6a ec 93 8e ec e9 ec ee 54 5a 8a ec a6",
     "31 30 f3 e5 00 f2 0c ed 35 00 f2 3b 00 f2 3c ef 3a 00 00 00 00 00"),
    ("ec 06 ee 26 6a ef 79 ed a9 56 3a d1 ba 9a ef 11 ec 29 8e 54 88 62 65 ed 59 8a ec a6",
     "f0 eb f2 3c f1 83 00 f2 21 f2 14 f4 aa 00 ef 40 ed a7 f3 63 00 ed 2d f3 59 00 00 00"),
    ("f2 3b 00 f2 3c ef 3a 4a 37 ae 11 df ee 25 60 52 5e 47 8a ec a6",
     "f2 3b 00 f2 3c ef 3a 00 ee 5b ed 9a 00 37 f3 e5 00 ef 94 f1 72"),
]

# 바이트 그대로 바꿔야 하는 것 — (찾을 바이트, 바꿀 바이트). 길이가 같아야 한다.
#   * `FC dx dy` 는 커서 이동이다. 원문은 첫 줄 16칸 자리에 숫자를 찍었는데
#     한국어 문구가 짧아져 그 자리가 글자 위로 왔다(제보 #5). 숫자를 한국어
#     빈칸(10칸)으로 옮기고, 바로 뒤 이동량을 같은 만큼 되돌려 '예/아니오'
#     위치는 그대로 둔다.
#   * 숫자 뒤의 글리프 0x1E1(機)은 번역이 안 돼 깨져 보인다 -> '기' (제보 #6)
BYTE_FIXUPS = {
    "THIRD/THIRD.WAR": [
        # 숫자 자리(`FC dx dy` + `F8 82`)는 이제 third-ui/foreign_recs.json 이
        # 직접 들고 있다 — 제3차·EX·트레이닝이 한꺼번에 맞는다.
        ("f8 82 ec e1", None),          # None = 뒤 2바이트를 '기' 로 (아래에서 처리)
    ] + _SAVE_HEADER_ANCHOR + _SORTIE_ROW + _ROSTER_ROW
      + _SORTIE_HEADER + _UNIT_TABS + _UPGRADE_MSG,
    "SECOND/SECOND.WAR": (_SAVE_HEADER_ANCHOR + _SORTIE_ROW + _ROSTER_ROW
                          + _SORTIE_HEADER + _UNIT_TABS + _UPGRADE_MSG),
    "EX/EX.WAR": (_SAVE_HEADER_ANCHOR + _SORTIE_ROW + _ROSTER_ROW
                  + _SORTIE_HEADER + _UNIT_TABS + _UPGRADE_MSG + _BUTTON_ROW),
    "TR.WAR": (_SAVE_HEADER_ANCHOR + _SORTIE_ROW + _ROSTER_ROW
               + _SORTIE_HEADER + _UNIT_TABS + _UPGRADE_MSG + _BUTTON_ROW),
    "EX/E_SCE.BIN": _EX_OBJECTIVES,
    # SLPS_020.70 은 세이브 머리글 라벨이 아직 원문(`セ-ブデ-タ`, 6칸)이라 앵커는
    # 그대로 두고, 출격 목록 열만 같이 당긴다.
    "SLPS_020.70": (_SORTIE_ROW + _ROSTER_ROW + _SORTIE_HEADER + _UNIT_TABS
                    + _UPGRADE_MSG + _UPGRADE_MSG_JP),
    # 제3차 시나리오 2·48 의 무언 대사 `エマ(‥‥‥‥)`. 원장 추출기가 `이름「」`
    # 봉투가 아니라고 버려서 번역 대상에 아예 없었다(2026-08-20 제보 #25).
    # 13바이트 자리에 13바이트로 넣는다 — `‥` 는 2바이트 글리프라 넷을 셋으로
    # 줄여야 들어간다(advance 8, 끝 phase 0 로 원문과 동일).
    "THIRD/3_SCE.BIN": [
        ("97 cd 3c eb f2 eb f2 eb f2 eb f2 3d ff",
         "f1 83 ef 06 3c eb f2 eb f2 eb f2 3d ff"),
    ],
}


def _byte_fixups(buf, name, ko_tab, log):
    """(총 교체 수). 패턴별 일치 횟수를 로그로 남긴다.

    옛 `KO_FIXUPS` 는 패턴이 **한 번도 안 맞아도 조용히 넘어갔다**. 그래서
    제2차·SLPS 의 출격 머리글이 바이트 배치가 달라 아예 안 맞는데도 몇 판이나
    모르고 지나갔다(제보 #9). 이제 0회도 로그에 남긴다 — 실패시키지는 않는다.
    한 자리의 여러 형태를 대안으로 늘어놓는 묶음(`_SORTIE_HEADER`)이 있어서
    0회가 정상인 패턴이 섞여 있기 때문이다. 실제 회귀 방지는 산출물의
    advance/phase 를 레트일과 대조하는 `audit/verify_ui_runs.py` 가 한다.
    """
    n = 0
    zero = 0
    for pat, rep in BYTE_FIXUPS.get(name, []):
        src = bytes.fromhex(pat)
        if rep is None:
            dst = src[:2] + _enc("기", ko_tab)
        else:
            dst = bytes.fromhex(rep)
        assert len(dst) == len(src), f"{name}: 바이트 길이가 다르다 {pat}"
        hit = 0
        at = buf.find(src)
        while at >= 0:
            buf[at:at + len(src)] = dst
            hit += 1
            at = buf.find(src, at + len(src))
        n += hit
        if hit == 0:
            zero += 1
    if zero:
        log(f"    (안 맞은 패턴 {zero}개 — 대안 형태라면 정상)")
    return n


def apply(files: dict, log=print) -> int:
    jp_tab = _jp_index()
    ko_tab = C.load_safe_glyph_map()
    total = 0
    for name, pairs in REPLACEMENTS.items():
        if name not in files:
            continue
        buf = bytearray(files[name])
        n = 0
        for jp, ko in sorted(pairs, key=lambda p: -len(p[0])):
            src = _enc(jp, jp_tab)
            dst = _enc(ko, ko_tab)
            if len(dst) > len(src):
                raise SystemExit(f"{name}: '{ko}' 가 '{jp}' 보다 {len(dst)-len(src)}바이트 깁니다")
            dst = dst + bytes(len(src) - len(dst))      # 남는 만큼 빈칸
            at = buf.find(src)
            while at >= 0:
                buf[at:at + len(src)] = dst
                n += 1
                at = buf.find(src, at + len(src))
        if n:
            files[name] = bytes(buf)
            log(f"  {name}: 원문 잔재 {n}곳 제자리 교체")
            total += n

    for name in set(list(KO_FIXUPS) + list(BYTE_FIXUPS)):
        if name not in files:
            continue
        buf = bytearray(files[name])
        n = 0
        for old, new in KO_FIXUPS.get(name, []):
            src, dst = _enc(old, ko_tab), _enc(new, ko_tab)
            if len(dst) > len(src):
                raise SystemExit(f"{name}: '{new}' 가 '{old}' 보다 깁니다")
            dst += bytes(len(src) - len(dst))
            at = buf.find(src)
            while at >= 0:
                buf[at:at + len(src)] = dst
                n += 1
                at = buf.find(src, at + len(src))
        n += _byte_fixups(buf, name, ko_tab, log)
        if n:
            files[name] = bytes(buf)
            log(f"  {name}: UI 문구·자리 {n}곳 손질")
            total += n
    return total


if __name__ == "__main__":
    from pathlib import Path
    fin = _P.BUILD / "final"
    files = {k: (fin / k.replace("/", "_")).read_bytes() for k in REPLACEMENTS
             if (fin / k.replace("/", "_")).exists()}
    print(f"교체 {apply(files)}곳")
