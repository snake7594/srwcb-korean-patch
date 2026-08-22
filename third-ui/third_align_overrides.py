# -*- coding: utf-8 -*-
"""Width-safe override translations for THIRD ui_master runs whose Korean
rendering advance exceeds (or phase-mismatches) the retail run before a
phase-sensitive anchor (FC/F8...). Keys are the RETAIL run text decoded with
the original font map (0x00 -> space). Values are candidate lists tried in
order; the aligner picks the first that fits (advance <= target, phase
reachable via the invisible EE FF blank + 0x00 padding).

Mirrors SECOND's discipline (tools/patch_second_exe_ui.py: _renderer_span_advance /
_encode_fixed_span_text): low glyph = 1 unit; high glyph = 1+phase, toggles phase.
"""

ALIGN_OVERRIDES = {
    " 出撃ユニット選択 あと":  [" 출격유닛선택 잔여"],
    "精神ポイント":            ["SP"],
    "消費精神ポイント":        ["소비 SP"],
    "作戦目的":                ["작전목적"],
    "勝利条件":                ["승리조건"],
    "敗北条件":                ["패배조건"],
    "反撃命令":                ["반격명령"],
    "精神検索一覧":            ["정신 검색"],
    "身代わり":                ["대역"],
    ":アニメ:":                [":애니:"],
    "    EN攻撃消費":          ["  EN공격소비"],
    "    EN防御消費":          ["  EN방어소비"],
    "HP吸収":                  ["HP흡수"],
    "イデオンゲ-ジ":           ["이데 수치"],
    "ツバゼリ":                ["경합"],
    "盾防御":                  ["방패"],
    "特殊操作":                ["특수조작"],
    "   修理費用":             ["   수리비용"],
    "修理費用":                ["수리비용"],
    "ユニット特別ボ-ナス":     ["유닛 특전"],
    "地形適応の":              ["지형적응 "],
    " ぁデ-タ":                [" ぁNO."],
    "デ-タ":                   ["NO."],
    "つきます。資金(":         ["붙음. 자금("],
    "されます。投入しますか?": ["됩니다. 투입?", "투입합니까?"],
    "いいえ":                  ["아뇨"],
    "強化パ-ツ選択":           ["강화파츠선택"],
    "強化パ-ツ装備":           ["강화파츠장착"],
    "登場作品 ":               ["등장작품 "],
    "ケ-ブル":                 ["연결"],
    "%  ケ-ブル":              ["%  연결"],
    "    ダメ-ジ":             ["    피해"],
    "登録キャラクタ-NO・":     ["등록 번호"],
    "サウンド":                ["음향"],
    "顔":                      ["얼"],          # (1,1): single high-glyph cell
    "登場作品":                ["등장작품"],
    # unit-status terrain-adaptation columns: single kanji in a 1-cell slot.
    # SECOND renders these as single syllables (공·육·해·우); the 2-syllable
    # forms (공중/육지/바다/우주) overflow the cell and squish two glyphs into
    # one — the "타입 글자 깨짐" the user reported.  Single Hangul = advance 1,
    # phase-1, byte-for-byte the retail 空/陸/海/宇 signature.
    "空":                      ["공"],
    "陸":                      ["육"],
    "海":                      ["해"],
    "宇":                      ["우"],
    # exact retail run texts discovered by the aligner's first pass
    "パイロット:":             ["PILOT:"],     # (6,0): 파일럿(3 highs)=phase-odd, unfixable
    "パイロット":              ["PILOT"],      # (5,0) ui[62] column header
    "    EN攻消費":            [" EN공격소비"],  # (10,1): fits via EE FF phase blank
    "    EN防消費":            [" EN방어소비"],
    "盾防":                    ["방패"],
    "レベルアップ レベル":     ["레벨업 레벨"],  # (10,0) via EE FF
}

# records rebuilt from RETAIL with ONLY these literal span replacements
# (date-picker grids: digits/layout must stay byte-exact)
SPECIAL_SPAN_RECORDS = {
    # ui[45] month picker @0x26ed4, ui[46] day picker @0x26f47
    0x26ed4: {"誕生日": "생일", "月": "월", "日": "일"},
    0x26f47: {"誕生日": "생일", "月": "월", "日": "일"},
}

# spirit-command descriptions wider than the screen wrap around (x overflow), so
# any description over 34 renderer units is replaced by these compact forms
SPIRIT_DESC_SHORT = {
    "指定したユニットのエネルギ-,残弾を最大まで補給します。ただし,補給されたパイロットの気力は-10されます。":
        "지정 유닛의 EN·탄약을 보급. 기력 -10",
    "すべての味方ユニットのHPを,最大HPの50%分回復します。":
        "모든 아군의 최대 HP 50%를 회복합니다.",
    "1タ-ンの間,攻撃の命中率が100%になります。ただし,相手が「ひらめき」を使っていた場合,「ひらめき」が優先されます。":
        "1턴간 명중률 100%. 「번뜩임」이 우선",
    "敵ユニットを倒さずに,HPを10だけ残します。ただし,相手より技量が上回っていなければ無効になります。":
        "적의 HP를 10만 남김. 기량 낮으면 무효",
    "指定した味方ユニットのHPを,最大HPの30%回復します。":
        "지정한 아군의 최대 HP 30%를 회복합니다.",
    "指定した行動済みユニット1体を,再び行動可能にします。":
        "행동을 마친 유닛 1기를 재행동시킴",
    "倒されたユニットを1体だけ復活します。選択可能です。":
        "격추된 유닛 1기를 부활(선택 가능)",
    "1タ-ンの間,敵から攻撃されなくなります。また,反撃も受けません。":
        "1턴간 적의 공격·반격을 받지 않음",
    "自爆し,隣接したユニット(味方含む)にHP分の防御無視ダメ-ジを与えます。":
        "자폭. 인접 유닛에 HP만큼 방어무시 피해",
    "一回だけ指定した味方ユニットの代わりに,マップ兵器以外の敵の攻撃を受けます。":
        "한 번만 지정 아군 대신 공격을 받음",
    "1タ-ンの間,敵の命中率が半分になります。ただし「必中」は優先されます。":
        "1턴간 적 명중률 절반. 「필중」이 우선",
    "1回だけ相手に与えるダメ-ジが3倍になります。「熱血」との併用はできません。":
        "1회 피해 3배. 「열혈」과 병용 불가",
    "マップ上にいる好きな味方キャラクタ-の精神コマンドを,通常の倍の精神ポイント消費で使えます。":
        "아군의 정신 커맨드를 SP 2배로 사용",
}

# every Korean string above (font keep-set must retain their glyph slots)
# '△□' forces the PS-button symbols into the extras tail so the button-config
# record can be repointed at them (their retail slots 0x8fa/0x8fb are Hangul now)
ALIGN_KO_TEXTS = [c for v in ALIGN_OVERRIDES.values() for c in v] + \
                 [v for d in SPECIAL_SPAN_RECORDS.values() for v in d.values()] + \
                 list(SPIRIT_DESC_SHORT.values()) + \
                 ["△□", "우주공륙수공수륙공육지중공지중",
                  "전환입체모노시작선택무", "전투BGM설정",   # type table + settings/buttons
                  "공육해우"]                                # single-syllable terrain columns
