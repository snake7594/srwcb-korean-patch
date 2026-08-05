# -*- coding: utf-8 -*-
"""inject_tr_ui.py 확장 — ex_extra_records.py 의 TR.WAR 적응판.

TR.WAR 은 EX.WAR 과 엔진·테이블이 1:1 이고 UI 테이블 본문은 헤더 4바이트만 빼면
바이트까지 동일하다(실측). 그래서 EX 에서 이미 폭 검증된 번역을 그대로 쓴다.

다만 오프셋은 다르다. 상수 델타로 밀면 틀리는 곳이 있어서(레코드 풀 안 위치는
제각각) **EX 레트일 바이트를 TR 레트일에서 유일 검색**해 위치를 잡는다.
"""

# --- 이식용 부트스트랩 (자동 삽입): 저장소 어디서 실행하든 동작 ---
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
# ------------------------------------------------------------------
import json, os, re, struct

SP = os.path.dirname(os.path.abspath(__file__))
ROOT = str(_P.WORK)
KANJI2KO = {"陸": "육", "空": "공", "水": "수", "宇": "우", "宙": "주",
            "地": "지", "中": "중"}
TYPE_TABLE, TYPE_COUNT = 0x095b4, 15     # EX 0x095b8 - 4
YESNO_OFF = 0x09718                      # EX 0x0971c - 4 ('無有')

# ex_extra_records.LOCAL_KO 와 동일 (EX 에서 폭 검증됨)
LOCAL_KO = {
    "セ-ブします。よろしいですか?": "저장합니다.<f6>괜찮습니까?",
    "セ-ブ終了しました。ゲ-ムを続けますか?": "저장을 마쳤습니다.<f6>게임을 계속할까요?",
    "ユニットの移動力を+2します。<f6>よろしいですか?": "유닛 이동력을 +2 합니다.<f6>괜찮습니까?",
    "ユニットのHPを+1500します。<f6>よろしいですか?": "유닛 HP를 +1500 합니다.<f6>괜찮습니까?",
    "ユニットの装甲を+600します。<f6>よろしいですか?": "유닛 장갑을 +600 합니다.<f6>괜찮습니까?",
    "ユニットのENを+100します。<f6>よろしいですか?": "유닛 EN을 +100 합니다.<f6>괜찮습니까?",
    "ユニットの運動性を+50します。<f6>よろしいですか?": "유닛 운동성을 +50 합니다.<f6>괜찮습니까?",
}
IN_PLACE_EXACT = {"モノラルステレオ": "모노입체"}


# ★레코드 끝은 반드시 토큰 문법으로 찾아야 한다. 컨트롤 코드의 '인자 바이트'가
#   0xFF 일 수 있어서, 단순히 첫 0xFF 를 찾으면 레코드를 짧게 잘라 읽는다
#   (맵 상태창 '방어/명중' 레코드가 32B 로 잘려 한글 50B 가 안 들어갔다).
CTRL_ARGS = {0xF6: 0, 0xF7: 0, 0xF8: 1, 0xF9: 1, 0xFA: 0, 0xFB: 2, 0xFC: 2, 0xFD: 2, 0xFE: 1}


def rec_end(buf, s):
    p = s
    while p < len(buf):
        x = buf[p]
        if x == 0xFF:
            return p + 1
        p += 1 if x < 0xEB else (2 if x <= 0xF5 else 1 + CTRL_ARGS.get(x, 0))
    return s


def _ko_to_rec(ko):
    return ko.replace("<f6>", "[F6]").replace("<f7>", "[F7]")


def _s32(buf, o):
    return struct.unpack_from("<i", buf, o)[0]


def patch_unit_types(war, retail, enc_ko, idx2ch):
    done = 0
    for k in range(TYPE_COUNT):
        f = TYPE_TABLE + 4 + 4 * k
        t = f + _s32(retail, f)
        chars, i = [], t
        while retail[i] != 0xFF:
            if retail[i] < 0xEB:
                i += 1; chars.append(None)
            else:
                idx = ((retail[i] - 0xEB) << 8) | retail[i + 1]
                chars.append(None if idx == 0x3FF else idx2ch.get(idx))
                i += 2
        kanji = [c for c in chars if c]
        if not kanji or any(c not in KANJI2KO for c in kanji):
            continue
        kb = enc_ko("".join(KANJI2KO[c] for c in kanji))
        assert len(kb) == 2 * len(kanji), (t, kanji)
        war[i - len(kb):i] = kb
        done += 1
    return done


def patch_yesno(war, retail, enc_ko, idx2ch):
    end = YESNO_OFF
    while retail[end] != 0xFF:
        end += 1 if retail[end] < 0xEB else 2
    src = retail[YESNO_OFF:end]
    kb = enc_ko("무유")
    if len(kb) != len(src):
        return 0
    war[YESNO_OFF:end] = kb
    return 1


def _field_index(buf):
    """필드상대 포인터 역색인: target -> [field, ...].

    ★4바이트 정렬로 훑으면 안 된다 — 실측하니 이 레코드들을 가리키는 필드는
    136개 중 131개가 오프셋 %4 == 1 이다(패킹된 구조체 안이라 정렬이 없다).
    정렬 스캔으로는 116건을 통째로 놓친다.
    """
    idx = {}
    n = len(buf)
    for f in range(0, n - 4):
        t = f + struct.unpack_from("<i", buf, f)[0]
        if 0x800 <= t < n:
            idx.setdefault(t, []).append(f)
    return idx


def relocate_pointed_records(war, retail, enc_ko, arena_alloc, verbose=True):
    """EX 에서 처리했던 잔여 레코드를 TR 에서 다시 찾아 번역·재배치한다.

    EX 레트일 바이트를 TR 레트일에서 **유일 검색**해 위치를 잡고, 그 위치를
    가리키는 필드상대 포인터가 정확히 1개일 때만 손댄다.
    """
    ex_ret = open(f"{ROOT}/extracted/EX/EX.WAR", "rb").read()
    lo = json.load(open(f"{_P.REPO}/ex-ui/data/ex_leftover.json", encoding="utf-8"))
    sp = json.load(open(f"{_P.REPO}/ex-ui/data/second_span_jp2ko.json", encoding="utf-8"))
    plain = lambda s: re.sub(r"<f[67]>|\[..\]|_", "", s)
    fidx = _field_index(retail)

    moved = skipped = ambiguous = 0
    for x in lo:
        if len(plain(x["jp"])) < 5:
            continue
        src = ex_ret[x["off"]:x["end"]]
        if len(src) < 6:
            skipped += 1; continue
        pos = retail.find(src)
        if pos < 0 or retail.count(src) != 1:
            ambiguous += 1; continue
        end = pos + len(src)
        if x["jp"] in IN_PLACE_EXACT:
            kb = enc_ko(IN_PLACE_EXACT[x["jp"]])
            if len(kb) == end - pos - 1 and bytes(war[pos:end]) == retail[pos:end]:
                war[pos:pos + len(kb)] = kb; moved += 1
            else:
                skipped += 1
            continue
        ko = LOCAL_KO.get(x["jp"]) or sp.get(x["jp"])
        if not ko:
            skipped += 1; continue
        fs = fidx.get(pos) or []
        if len(fs) != 1:
            skipped += 1; continue
        field = fs[0]
        if bytes(war[pos:end]) != retail[pos:end]:
            skipped += 1; continue
        if field + struct.unpack_from("<i", war, field)[0] != pos:
            skipped += 1; continue
        kb = enc_ko(_ko_to_rec(ko)) + b"\xFF"
        npos = arena_alloc(len(kb))
        war[npos:npos + len(kb)] = kb
        struct.pack_into("<i", war, field, npos - field)
        moved += 1
    if verbose:
        print(f"  잔여 레코드 도너 재배치 {moved}건 (미처리 {skipped}, 위치모호 {ambiguous})")
    return moved


# TR 에만 다른 외래 레코드. 시스템 메뉴 마지막 항목이 EX 는 'セ-ブ'(저장)인데
# 트레이닝 모드는 ' 終了'(종료)다. 앞 24바이트가 TR 안에서 유일하므로 그걸로 위치를
# 잡고, 제3차/EX 에서 폭 검증된 한글 레코드의 '저장'만 '종료'로 바꿔 쓴다.
# (둘 다 전각 2자 = 4바이트라 길이·phase 가 그대로다.)
FOREIGN_VARIANT = {0x1a1d1: {"prefix": 24, "ko_swap": ("저장", "종료")}}


def patch_foreign_records(war, retail, enc_ko, verbose=True):
    """ui_master 포인터 표에 없는 맵/시스템 레코드 5개.

    제3차에서 폭 검증된 번역(foreign_recs.pkl)을 EX 경유로 TR 위치에 찍는다.
    패딩은 반드시 '마지막 F6 뒤'에 넣는다(끝에 넣으면 창이 안 그려진다).
    """
    import pickle
    ex_ret = open(f"{ROOT}/extracted/EX/EX.WAR", "rb").read()
    F = pickle.load(open(f"{_P.REPO}/third-ui/foreign_recs.pkl", "rb"))
    TH2EX = 0xbd39

    def mid_pad(body, pad):
        if pad <= 0:
            return bytes(body)
        i = body.rfind(b"\xF6")
        return bytes(body) + b"\x00" * pad if i < 0 else \
            bytes(body[:i + 1]) + b"\x00" * pad + bytes(body[i + 1:])

    done = miss = variant = 0
    for tt, kb in F.items():
        et = tt - TH2EX
        src = ex_ret[et:rec_end(ex_ret, et)]
        body = bytes(kb[:-1] if kb[-1] == 0xFF else kb)
        pos = retail.find(src) if retail.count(src) == 1 else -1
        if pos < 0:
            v = FOREIGN_VARIANT.get(et)
            if not v:
                miss += 1; continue
            head = src[:v["prefix"]]
            if retail.count(head) != 1:
                miss += 1; continue
            pos = retail.find(head)
            a, b = enc_ko(v["ko_swap"][0]), enc_ko(v["ko_swap"][1])
            assert len(a) == len(b) and body.count(a) == 1, "변형 치환이 모호함"
            body = body.replace(a, b)
            variant += 1
        orig = rec_end(retail, pos) - pos
        assert war[pos + orig - 1] == 0xFF
        if len(body) > orig - 1:
            miss += 1; continue
        war[pos:pos + orig - 1] = mid_pad(body, orig - 1 - len(body))
        done += 1
    if verbose:
        print(f"  외래 맵/시스템 레코드 {done}개 제자리 번역 "
              f"(TR 변형 {variant}, 미매칭 {miss})")
    return done


def patch_bmess_tables(war, retail, tables, verbose=True):
    """실행파일에 박힌 BMESS2/3/4 외부 오프셋표를 재패킹본의 표로 교체.

    ★ 이걸 빼먹으면 첫 전투 메시지 로드가 CPE 블록 중간에서 시작해
      대사가 안 나오고 전투가 그대로 멈춘다(트레이닝 모드에서 실제로 발생).
    """
    done = 0
    for name, old_tbl, new_tbl in tables:
        assert len(old_tbl) == len(new_tbl), f"{name}: 표 크기 변동"
        off = retail.find(old_tbl)
        assert off >= 0 and retail.count(old_tbl) == 1, f"{name}: 표가 유일하지 않음"
        assert bytes(war[off:off + len(old_tbl)]) == old_tbl, f"{name}: 이미 변경됨"
        war[off:off + len(new_tbl)] = new_tbl
        done += 1
        if verbose:
            print(f"  BMESS 외부표 {name} @{off:#x} ({len(new_tbl)}B) 갱신")
    return done
