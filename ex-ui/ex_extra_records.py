# -*- coding: utf-8 -*-
"""inject_ex_ui.py 확장 — 주입기가 놓쳤던 EX.WAR 잔여 텍스트.

세 갈래로 처리한다.

 1) 유닛 타입 테이블 @0x095b8 (15엔트리)
    한자 1자 = 한글 1자라 바이트 수가 같다. 우측정렬 슬롯의 뒤쪽을 그대로 덮어쓴다.
    (기존 하드코딩 _TYPES 는 10개뿐이라 陸/空/空/水陸 4개가 일본어로 남아
     유닛 능력창 '타입' 칸이 엉뚱한 글리프로 나왔다.)

 2) 無有 @0x0971c — 한자 2자 = 한글 2자, 제자리.

 3) 필드상대 포인터(target = field + s32(field))가 정확히 1개인 레코드는
    도너로 옮기고 포인터만 재조준한다. 캐릭터 사전·작품명·데모/BGM 제목
    (0xb433~0xbca7)과 저장/유닛 강화 확인 메시지가 여기 해당한다.
    번역문은 제2차에서 이미 폭 검증된 것을 재사용한다.
"""
import json, os, re, struct

SP = os.path.dirname(os.path.abspath(__file__))

KANJI2KO = {"陸": "육", "空": "공", "水": "수", "宇": "우", "宙": "주",
            "地": "지", "中": "중"}
TYPE_TABLE, TYPE_COUNT = 0x095b8, 15
YESNO_OFF = 0x0971c                      # '無有'

# 원장에 없던 확인 메시지 (제2차 표기·폭 규칙에 맞춰 새로 번역)
LOCAL_KO = {
    "セ-ブします。よろしいですか?": "저장합니다.<f6>괜찮습니까?",
    "セ-ブ終了しました。ゲ-ムを続けますか?": "저장을 마쳤습니다.<f6>게임을 계속할까요?",
    "ユニットの移動力を+2します。<f6>よろしいですか?": "유닛 이동력을 +2 합니다.<f6>괜찮습니까?",
    "ユニットのHPを+1500します。<f6>よろしいですか?": "유닛 HP를 +1500 합니다.<f6>괜찮습니까?",
    "ユニットの装甲を+600します。<f6>よろしいですか?": "유닛 장갑을 +600 합니다.<f6>괜찮습니까?",
    "ユニットのENを+100します。<f6>よろしいですか?": "유닛 EN을 +100 합니다.<f6>괜찮습니까?",
    "ユニットの運動性を+50します。<f6>よろしいですか?": "유닛 운동성을 +50 합니다.<f6>괜찮습니까?",
}

# 한 레코드 안에 고정 폭 라벨이 여러 개 들어 있어 재배치하면 stride 가 깨지는 것들.
# 바이트 수가 정확히 같을 때만 제자리 교체한다 (모노=4B, 입체=4B → ステレオ 와 동일).
IN_PLACE_EXACT = {
    "モノラルステレオ": "모노입체",
}


def _ko_to_rec(ko):
    """'<f6>' 표기를 주입기 enc_ko 가 아는 '[F6]' 표기로."""
    return ko.replace("<f6>", "[F6]").replace("<f7>", "[F7]")


def patch_unit_types(war, retail, enc_ko, idx2ch):
    """타입 테이블 15엔트리를 한자 1:1 한글로 제자리 교체. 반환: 교체 수."""
    def s32(buf, o): return struct.unpack_from("<i", buf, o)[0]
    done = 0
    for k in range(TYPE_COUNT):
        f = TYPE_TABLE + 4 + 4 * k
        t = f + s32(retail, f)
        # retail 레코드에서 한자만 모은다 (앞쪽 EE FF 패딩은 그대로 둔다)
        chars, i = [], t
        while retail[i] != 0xFF:
            if retail[i] < 0xEB:
                i += 1; chars.append(None)          # 저글리프(패딩 아님) — 건드리지 않음
            else:
                idx = ((retail[i] - 0xEB) << 8) | retail[i + 1]
                chars.append(None if idx == 0x3FF else idx2ch.get(idx))
                i += 2
        kanji = [c for c in chars if c]
        if not kanji or any(c not in KANJI2KO for c in kanji):
            continue
        kb = enc_ko("".join(KANJI2KO[c] for c in kanji))
        assert len(kb) == 2 * len(kanji), (t, kanji)
        war[i - len(kb):i] = kb                     # 우측정렬: FF 바로 앞에 붙인다
        done += 1
    return done


def patch_yesno(war, retail, enc_ko, idx2ch):
    """'無有' -> '무유' 제자리."""
    end = YESNO_OFF
    while retail[end] != 0xFF:
        end += 1 if retail[end] < 0xEB else 2
    src = retail[YESNO_OFF:end]
    kb = enc_ko("무유")
    if len(kb) != len(src):
        return 0
    war[YESNO_OFF:end] = kb
    return 1


def relocate_pointed_records(war, retail, enc_ko, arena_alloc, verbose=True):
    """필드상대 포인터가 1개인 잔여 레코드를 번역해 도너로 옮기고 포인터 재조준."""
    lo = json.load(open(f"{SP}/ex/ex_leftover.json", encoding="utf-8"))
    refs = json.load(open(f"{SP}/ex/ex_leftover_refs.json", encoding="utf-8"))
    sp = json.load(open(f"{SP}/ex/second_span_jp2ko.json", encoding="utf-8"))
    plain = lambda s: re.sub(r"<f[67]>|\[..\]|_", "", s)
    moved, skipped = 0, 0
    for x in lo:
        if len(plain(x["jp"])) < 5:
            continue
        if x["jp"] in IN_PLACE_EXACT:                 # stride 보존이 필요한 것
            kb = enc_ko(IN_PLACE_EXACT[x["jp"]])
            if len(kb) == x["end"] - x["off"] - 1 and bytes(war[x["off"]:x["end"]]) == retail[x["off"]:x["end"]]:
                war[x["off"]:x["off"] + len(kb)] = kb; moved += 1
            else:
                skipped += 1
            continue
        rf = refs.get(hex(x["off"]))
        if not rf or len(rf) != 1:
            skipped += 1; continue
        ko = LOCAL_KO.get(x["jp"]) or sp.get(x["jp"])
        if not ko:
            skipped += 1; continue
        field = int(rf[0], 16)
        # 원본이 그대로인지(=주입기가 이미 손대지 않았는지) 확인
        if bytes(war[x["off"]:x["end"]]) != retail[x["off"]:x["end"]]:
            skipped += 1; continue
        if field + struct.unpack_from("<i", war, field)[0] != x["off"]:
            skipped += 1; continue
        kb = enc_ko(_ko_to_rec(ko)) + b"\xFF"
        pos = arena_alloc(len(kb))
        war[pos:pos + len(kb)] = kb
        struct.pack_into("<i", war, field, pos - field)
        moved += 1
    if verbose:
        print(f"  잔여 레코드 도너 재배치 {moved}건 (미처리 {skipped})")
    return moved
