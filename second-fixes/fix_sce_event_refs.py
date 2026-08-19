# -*- coding: utf-8 -*-
"""제2차 시나리오 브리핑 프리즈 수정 — pool 내부 이벤트 스크립트의 B1/B3/B4
필드상대 포인터를 재조준한다.

근본원인(역분석·diff 확증): 재빌드(rebuild_second_sce)는 프리풀 스크립트
(block_start..pool_start)의 텍스트 참조만 재배치한다. 그런데 각 시나리오의
브리핑 대화를 구동하는 **이벤트 스크립트는 pool 안(주로 records[0])에** 있고,
그 안의 B3/B4 대화 포인터는 재배치 대상에서 누락됐다. 한글 번역으로 pool 이
커져 대화 레코드가 이동했는데 변위는 레트일 그대로 → 전부 mid-record 를 가리켜
대화가 진행되지 못하고 정지(8화에서 확인, 21개 시나리오 공통).

수정 방식(최소 변경): 이미 빌드된 2_SCE 에서, 레트일 기준으로 각 참조가 가리키던
'레코드 서수(ordinal)'를 알아내고, 배포본의 같은 서수 레코드의 새 시작 위치로
변위(u16)만 다시 쓴다. 다른 바이트는 건드리지 않는다.

레트일에서 '레코드 시작을 가리키는' B1/B3/B4 만 진짜 이벤트 참조로 인정한다
(find_text_references 와 같은 휴리스틱). host 레코드 안에서 참조의 상대 위치는
레트일·배포본이 같아야 하며(이벤트 스크립트 본문은 재번역되지 않음), op 바이트
일치로 검증한다.
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
for _sub in ("tools", "third-ui", "ex-ui", "tr-ui", "audit", "menu-align", "second-fixes"):
    _p = _os.path.join(_d, _sub)
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import sys, math, struct
from pathlib import Path

R = str(_P.WORK)
sys.path.insert(0, f"{R}/tools")
from analyze_sce_relocation import (parse_scenarios, TEXT_POINTER_OPCODES,
                                    ARG_POINTER_FORMS, iter_pointer_sites,
                                    build_anchor_context, resolve_inner_anchor)

#: 안쪽 앵커 재조준에서 뺄 포인터 형태. `B6 00` 은 레코드 시작 적중률이 67%뿐이라
#: 잡음이 많다(f05). 레트일 실측으로 이 형태의 앵커 후보 146곳 중 화자이름 필터를
#: 통과하는 건 EX sc37(`エマ「?‥‥何か外がさわがしいわね`) 단 1곳이라, 빼도
#: 잃는 게 거의 없다. 나중에 확인되면 이 집합만 비우면 된다.
ANCHOR_SKIP_FORMS = frozenset(((0xB6, 0x00),))


def operand_offset(buf, off):
    """이 자리가 대사 포인터면 **피연산자 위치**, 아니면 None.

    `B1/B3/B4` 는 옵코드 바로 뒤, `B6 00`/`B6 01`/`B9 03`/`B9 08` 은 옵코드+2 다.
    """
    op = buf[off]
    if op in TEXT_POINTER_OPCODES:
        return off + 1
    if off + 1 < len(buf) and (op, buf[off + 1]) in ARG_POINTER_FORMS:
        return off + 2
    return None


def scan_pool_refs(buf, scn):
    """pool 레코드 영역에서 target 이 레코드 시작인 대사 포인터.

    `B1/B3/B4 <변위16>` 과 `B6 00 <변위16>` 두 형태를 모두 본다 — 뒤엣것은
    피연산자가 옵코드+2 에 있다(핸들러가 커서를 1 늘린 뒤 읽는다).
    반환값의 세 번째 원소는 **피연산자 위치**다.
    """
    starts = {r.start for r in scn.records}
    refs = []
    for off, operand, op in iter_pointer_sites(buf, scn.pool_start,
                                               scn.record_data_end):
        disp = struct.unpack_from("<h", buf, operand)[0]
        tgt = operand + disp
        if tgt in starts:
            refs.append((off, op, tgt))
    return refs


def record_index_containing(scn, off):
    for ri, r in enumerate(scn.records):
        if r.start <= off < r.end:
            return ri
    return None


def record_ordinal_of_start(scn, start):
    for ri, r in enumerate(scn.records):
        if r.start == start:
            return ri
    return None


def retarget(ko_bytes, jp_bytes, *, apply=False, verbose=True):
    ko = bytearray(ko_bytes)
    bj = parse_scenarios(jp_bytes)
    bk = parse_scenarios(ko)
    assert len(bj) == len(bk), "시나리오 수 불일치"
    total = fixed = already = spurious = 0
    problems = []
    spurious_list = []
    for si in range(len(bj)):
        sj, sk = bj[si], bk[si]
        if len(sj.records) != len(sk.records):
            problems.append(f"sc{si}: 레코드 수 {len(sj.records)}!={len(sk.records)}")
            continue
        for off_j, op, tgt_j in scan_pool_refs(jp_bytes, sj):
            total += 1
            host = record_index_containing(sj, off_j)
            tgt_ord = record_ordinal_of_start(sj, tgt_j)
            if host is None or tgt_ord is None:
                problems.append(f"sc{si} @{off_j:#x}: host/target 매핑 실패")
                continue
            off_in_rec = off_j - sj.records[host].start
            off_k = sk.records[host].start + off_in_rec
            # host 레코드 안 상대 위치가 배포본에서 같은 op 가 아니면, 이건 진짜
            # 이벤트 스크립트 참조가 아니라 '텍스트 대사 레코드 안의 우연 매치'다
            # (텍스트는 재번역돼 위치가 밀림). 이벤트 스크립트 본문은 불변이라 항상
            # op 가 일치한다. → 우연 매치는 게임이 참조로 쓰지 않으므로 스킵해도 무해.
            if off_k + 3 > len(ko) or ko[off_k] != op:
                spurious += 1
                spurious_list.append(f"sc{si} rec[{host}]+{off_in_rec:#x} op{op:#x}")
                continue
            new_tgt = sk.records[tgt_ord].start
            opnd_k = operand_offset(ko, off_k)
            if opnd_k is None:
                spurious += 1
                continue
            new_disp = new_tgt - opnd_k
            if not (-0x8000 <= new_disp <= 0x7FFF):
                problems.append(f"sc{si} @{off_k:#x}: 변위 범위초과 {new_disp:#x}")
                continue
            cur_disp = struct.unpack_from("<h", ko, opnd_k)[0]
            cur_tgt = opnd_k + cur_disp
            if cur_tgt == new_tgt:
                already += 1
                continue
            if apply:
                struct.pack_into("<h", ko, opnd_k, new_disp)
            fixed += 1
    if verbose:
        print(f"  이벤트 참조 총 {total}  재조준필요 {fixed}  이미정상 {already}  "
              f"우연매치(스킵) {spurious}  문제 {len(problems)}")
        for s in spurious_list[:8]:
            print("   ~ 우연매치 스킵:", s)
        for p in problems[:20]:
            print("   !!", p)
    return bytes(ko), fixed, problems


def retarget_prepool(ko_bytes, jp_bytes, *, apply=False, verbose=True, game=""):
    """**풀 앞 스크립트**(block_start..pool_start)의 대사 포인터를 재조준한다.

    이 구간은 번역으로 바뀌지 않아 배포본과 레트일의 바이트 오프셋이 같다.
    그래서 같은 자리에서 같은 옵코드를 확인하고 변위만 다시 쓰면 된다.

    2026-08-19 추가 — 목표가 레코드 시작이 아니라 **레코드 안쪽 메시지 시작**인
    포인터(제3차 12·EX 45곳)도 여기서 함께 맞춘다. 레코드를 쪼개지 않고
    배포본 레코드 안에서 `k번째 「 앞 화자 이름의 시작`을 다시 찾는다
    (`analyze_sce_relocation.jp_message_anchor` 참고). 번역 원장은 손대지 않는다.
    """
    ko = bytearray(ko_bytes)
    bj = parse_scenarios(jp_bytes)
    bk = parse_scenarios(ko)
    ctx = build_anchor_context(jp_bytes, ko_bytes, bj, bk)
    total = fixed = already = skipped = anchors = 0
    problems = []
    weak = []
    for si, (sj, sk) in enumerate(zip(bj, bk)):
        if len(sj.records) != len(sk.records):
            continue
        if (sj.pool_start - sj.block_start) != (sk.pool_start - sk.block_start):
            continue
        starts_j = {r.start: i for i, r in enumerate(sj.records)}
        for off_j, opnd_j, op in iter_pointer_sites(jp_bytes, sj.block_start,
                                                    sj.pool_start):
            tgt_j = opnd_j + struct.unpack_from("<h", jp_bytes, opnd_j)[0]
            ordn = starts_j.get(tgt_j)
            res = None
            if ordn is None:
                # ★ 레코드 '안쪽 메시지 시작'을 겨누는 포인터
                if (op, jp_bytes[off_j + 1]) in ANCHOR_SKIP_FORMS:
                    continue
                host = next((i for i, r in enumerate(sj.records)
                             if r.start < tgt_j < r.end), None)
                if host is None:
                    continue
                res = resolve_inner_anchor(jp_bytes, ko_bytes, sj.records[host],
                                           sk.records[host], tgt_j, ctx,
                                           game, off_j)
                if res is None:
                    continue                 # 앵커가 아니다 — 우연히 맞은 바이트
            total += 1
            off_k = sk.block_start + (off_j - sj.block_start)
            if off_k + 4 > len(ko) or ko[off_k] != op or operand_offset(ko, off_k) is None:
                skipped += 1                 # 구조가 다르면 손대지 않는다
                continue
            opnd_k = off_k + (opnd_j - off_j)
            if res is None:
                new_tgt = sk.records[ordn].start
            else:
                new_tgt = res["offset"]
                anchors += 1
                if res["how"] not in ("namemap", "lexicon", "override"):
                    weak.append(f"sc{si} @{off_j:#x} {res['how']}")
            new_disp = new_tgt - opnd_k
            if not (-0x8000 <= new_disp <= 0x7FFF):
                problems.append(f"sc{si} @{off_k:#x}: 변위 범위초과 {new_disp:#x}")
                continue
            if struct.unpack_from("<h", ko, opnd_k)[0] == new_disp:
                already += 1
                continue
            if apply:
                struct.pack_into("<h", ko, opnd_k, new_disp)
            fixed += 1
    if verbose:
        print(f"  풀앞 스크립트 참조 총 {total}  재조준 {fixed}  이미정상 {already}  "
              f"건너뜀 {skipped}  안쪽앵커 {anchors}  문제 {len(problems)}")
        for w in weak[:10]:
            print("   ~ 앵커 확신 낮음:", w)
        for p in problems[:10]:
            print("   !!", p)
    return bytes(ko), fixed, problems


def rewrap_anchor_tails(ko_bytes, jp_bytes, *, apply=False, verbose=True, game=""):
    """앵커부터 그리는 꼬리의 줄바꿈을 다시 잡는다 (0x00 <-> 0xF6, 길이 불변).

    자동 줄바꿈은 레코드를 처음부터 그린다고 보고 F6 을 놓는다. 앵커 포인터는
    레코드 중간부터 그리므로 꼬리 첫 줄이 상자를 넘는다(57곳 중 5곳).
    KO 장면 레코드의 F6 은 반각공백을 대신 쓴 것이라, 맞바꾸기만 하면 길이가
    안 변해 재배치·원장·게이트가 그대로다.
    """
    from second_translation_codec import (glyph_advance, MAX_SCENE_ADVANCE,
                                          MAX_PAGE_LINES, record_geometry)
    from analyze_sce_relocation import (parse_scenarios, iter_pointer_sites,
                                        build_anchor_context, resolve_inner_anchor,
                                        tokenize_record)
    ko = bytearray(ko_bytes)
    bj, bk = parse_scenarios(jp_bytes), parse_scenarios(ko)
    ctx = build_anchor_context(jp_bytes, ko_bytes, bj, bk)
    touched = 0
    for sj, sk in zip(bj, bk):
        if len(sj.records) != len(sk.records):
            continue
        if (sj.pool_start - sj.block_start) != (sk.pool_start - sk.block_start):
            continue
        starts_j = {r.start for r in sj.records}
        for off_j, opnd_j, op in iter_pointer_sites(jp_bytes, sj.block_start,
                                                    sj.pool_start):
            tgt = opnd_j + struct.unpack_from("<h", jp_bytes, opnd_j)[0]
            if tgt in starts_j or (op, jp_bytes[off_j + 1]) in ANCHOR_SKIP_FORMS:
                continue
            host = next((i for i, r in enumerate(sj.records)
                         if r.start < tgt < r.end), None)
            if host is None:
                continue
            res = resolve_inner_anchor(jp_bytes, ko_bytes, sj.records[host],
                                       sk.records[host], tgt, ctx, game, off_j)
            if res is None or res["how"] == "record-start":
                continue
            s, e = res["offset"], sk.records[host].end
            # 상자는 **레트일 꼬리가 실제로 쓴 크기**를 따른다. 레코드 전체로
            # 재면 안 된다 — 머리(시스템문)는 더 넓은 창에서 그려져서 폭이
            # 60~276 까지 나오고, 그 값을 꼬리에 쓰면 꼬리가 전혀 안 접힌다.
            _w, _h = record_geometry(bytes(jp_bytes[tgt:sj.records[host].end]))
            CAP = max(_w, MAX_SCENE_ADVANCE)
            MAX_LINES = max(_h, MAX_PAGE_LINES)
            toks = tokenize_record(ko, s, e)
            adv = phase = 0
            lines = 1
            changed = False
            for i, (off, kind, v) in enumerate(toks):
                breakable = (kind == 'g' and v == 0x00) or (kind == 'c' and v == 0xF6)
                if not breakable:
                    if kind == 'g':
                        st, phase = glyph_advance(v, phase)
                        adv += st
                    continue
                w, ph = 0, phase          # 다음 끊을 자리까지의 폭을 미리 잰다
                for o2, k2, v2 in toks[i + 1:]:
                    if k2 == 'e' or (k2 == 'g' and v2 == 0x00) or (k2 == 'c' and v2 == 0xF6):
                        break
                    if k2 == 'g':
                        st, ph = glyph_advance(v2, ph)
                        w += st
                # 잘림은 정수 칸이 아니라 **반칸**으로 정해진다 —
                # `31칸 + 반칸`(=63)은 정수로는 합격이지만 4px 잘린다(#14b).
                if 2 * (adv + 1 + w) + ph > 2 * CAP and lines < MAX_LINES:
                    want, adv, phase, lines = 0xF6, 0, 0, lines + 1
                else:
                    want = 0x00
                    st, phase = glyph_advance(0x00, phase)
                    adv += st
                if ko[off] != want:
                    changed = True
                    if apply:
                        ko[off] = want
            touched += 1 if changed else 0
    if verbose:
        print(f"  앵커 꼬리 재래핑: {touched}곳")
    return bytes(ko), touched


def scan_f0_refs(buf, scn):
    """`F0 <b> <u16>` — 풀 시작 기준 **바이트 오프셋**으로 대사를 가리키는 참조.

    대화의 시작 지점을 이걸로 잡는다. B1/B3/B4(필드 상대)만 재조준하고 이건
    원문 값 그대로 두면, 한글로 풀이 커진 만큼 엉뚱한 자리에 떨어져 대사가
    문장 중간부터 나오고 화자가 사라진다(제3차 8화).

    레트일에서 **레코드 시작을 정확히 가리키는 것만** 진짜 참조로 인정한다.
    """
    rel = {r.start - scn.pool_start: i for i, r in enumerate(scn.records)}
    out = []
    for off in range(scn.block_start, scn.pool_start - 3):
        if buf[off] != 0xF0:
            continue
        v = struct.unpack_from("<H", buf, off + 2)[0]
        if v > 0 and v in rel:
            out.append((off, v, rel[v]))
    return out


def retarget_f0(ko_bytes, jp_bytes, *, apply=False, verbose=True):
    """★ 쓰지 말 것 — 검출이 통계적으로 무의미하다 (2026-08-18 재측정).

    풀 앞 스크립트의 0xF0 은 맵·유닛 데이터의 흔한 채움값이라 EX 만 27,811개다.
    그중 off+2 의 u16 이 레코드 시작과 맞는 비율은 **1.85%** 로, 전 바이트 기준선
    1.11% 와 사실상 같다(진짜 포인터 형태인 B1/B2/B3/B4/B6 00/B9 03 은 94~100%).
    즉 드라이런이 내놓는 '재조준 필요 474곳'은 전부 오탐이다.

    `apply=True` 로 배선하면 EX 474곳, 제2차 152곳, 제3차 276곳의 **멀쩡한 데이터
    바이트**를 덮어쓴다. 지금 빌드가 이 함수를 한 번도 부르지 않아서 무사한 것이니,
    '재조준이 이렇게 많이 남았네' 하고 연결하지 말 것.
    """
    ko = bytearray(ko_bytes)
    bj = parse_scenarios(jp_bytes)
    bk = parse_scenarios(ko)
    total = fixed = already = skipped = 0
    problems = []
    left = []
    for si, (sj, sk) in enumerate(zip(bj, bk)):
        # 레코드 수가 안 맞는 시나리오는 서수 대응이 불가능하다. 경계 안정화가
        # 손댈 수 없어 남긴 시나리오가 여기 해당한다 — 건드리지 않고 넘어간다.
        if len(sj.records) != len(sk.records):
            left.append(si)
            continue
        if (sj.pool_start - sj.block_start) != (sk.pool_start - sk.block_start):
            left.append(si)
            continue
        for off_j, val, ordn in scan_f0_refs(jp_bytes, sj):
            total += 1
            off_k = sk.block_start + (off_j - sj.block_start)
            if ko[off_k] != 0xF0:                 # 구조가 다르면 손대지 않는다
                skipped += 1
                continue
            new = sk.records[ordn].start - sk.pool_start
            if not (0 < new <= 0xFFFF):
                problems.append(f"sc{si} @{off_j:#x}: 범위 초과 {new:#x}")
                continue
            if struct.unpack_from("<H", ko, off_k + 2)[0] == new:
                already += 1
                continue
            if apply:
                struct.pack_into("<H", ko, off_k + 2, new)
            fixed += 1
    if verbose:
        print(f"  F0 풀상대 참조 총 {total}  재조준 {fixed}  이미정상 {already}  "
              f"건너뜀 {skipped}  손 못 댄 시나리오 {left}  문제 {len(problems)}")
        for p in problems[:10]:
            print("   !!", p)
    return bytes(ko), fixed, problems


def _bad_operand_targets(buf, scn):
    """피연산자에 0xFF 가 들어간 유효 포인터의 타깃 목록.

    B1/B3/B4 는 뒤에 2바이트 변위를 달고 다니는데, 레코드를 훑는 문법은 그 두
    바이트도 글리프로 읽는다. 거기에 0xFF 가 있으면 레코드가 거기서 끝난 것처럼
    보여 뒤쪽 레코드 번호가 통째로 밀린다(작전목적의 승리/패배조건이 엉뚱하게
    나오던 원인). 레트일에는 이런 자리가 없다.
    """
    starts = {r.start for r in scn.records}
    out = set()
    for off in range(scn.pool_start, scn.record_data_end - 3):
        opnd = operand_offset(buf, off)
        if opnd is None:
            continue
        if 0xFF not in buf[opnd:opnd + 2]:
            continue
        tgt = opnd + struct.unpack_from("<H", buf, opnd)[0]
        if tgt in starts:
            out.add(tgt)
    return out


def harden_against_ff_operands(src, replacements, rebuild, *, rounds=400, verbose=True):
    """레코드 경계가 레트일과 똑같이 나오는 배치가 될 때까지 다시 빌드한다.

    작전목적 화면은 조건문을 **레코드 순번**으로 집어 온다. 그런데 B1/B3/B4 의
    2바이트 변위에 0xFF 가 끼면 레코드를 훑는 쪽에서는 거기서 레코드가 끝난 것처럼
    보여 그 뒤 순번이 통째로 밀린다(승리조건 자리에 엉뚱한 글이 나오던 원인).

    풀에 여유 바이트가 없어 다 만든 뒤에는 밀 수가 없다. 그래서 어긋난 자리 **앞
    레코드를 한 칸 늘려**(줄 끝 공백 하나) 처음부터 다시 배치하고, 경계가 레트일과
    같아질 때까지 되풀이한다.
    """
    repl = dict(replacements)
    added = 0
    stuck: dict[int, int] = {}
    # ★ 이벤트 스크립트 레코드에는 절대 손대지 않는다. 스크립트는 VM 이 그대로
    #    실행하는 바이트열이라 한 바이트만 끼워도 게임이 멈춘다(8화 프리즈).
    #    번역으로 교체하는 레코드(= 순수 텍스트)만 줄 끝 공백을 하나 붙인다.
    safe = set(replacements)
    unsafe: set[int] = set()          # 스크립트뿐이라 손댈 수 없는 시나리오
    # ★ 작전목적(승리/패배조건) 블록에는 **빈 줄(F6)을 붙이면 안 된다** — 작전목적
    #   창이 깨지고 게임이 멈춘다(2026-08-09 제보, 제3차 8화). 다만 이 블록을
    #   통째로 빼면 경계를 못 맞춰 조건문 순번이 어긋난다. 그래서 여기서는
    #   줄 끝 공백만 허용한다(줄 수 불변).
    from analyze_sce_relocation import objective_block_records as _OBJ
    objective = _OBJ(bytes(src))

    def _pad_byte(raw, allow_newline=True):
        """레코드를 한 바이트 늘릴 때 **화면을 안 건드리는** 바이트를 고른다.

        예전엔 무조건 반각 공백을 끝에 붙였다. 그런데 그 줄이 이미 상자(32칸)를
        꽉 채웠으면 33칸이 되어 화면에서 마지막 글자가 잘린다. 쪽에 줄 여유가
        있으면 빈 줄(F6)을 붙이는 게 낫다 — 폭을 전혀 안 건드린다.
        """
        from second_translation_codec import glyph_advance, CONTROL_ARGUMENT_BYTES as CA
        adv = mx = 0
        lines = 1
        maxlines = 1
        phase = 0
        q = 0
        while q < len(raw):
            b = raw[q]
            if b == 0xFF:
                break
            if b < 0xEB:
                idx, q = b, q + 1
            elif b <= 0xF5:
                idx, q = ((b - 0xEB) << 8) | raw[q + 1], q + 2
            else:
                if b in (0xF6, 0xF7):
                    mx = max(mx, adv); adv = 0; phase = 0
                    if b == 0xF6:
                        lines += 1
                        maxlines = max(maxlines, lines)
                    else:
                        lines = 1
                q += 1 + CA.get(b, 0)
                continue
            step, phase = glyph_advance(idx, phase)
            adv += step
        mx = max(mx, adv)
        if maxlines < 3 and allow_newline:
            return bytes([0xF6])   # 빈 줄 — 폭이 안 늘어난다
        if mx < 32:
            return bytes(1)      # 줄 끝 공백 — 상자 안에 여유가 있을 때만
        return None              # 이 레코드는 건드리면 화면이 깨진다

    def _has_room(rec):
        """이 레코드를 한 바이트 늘려도 화면이 안 깨지는가."""
        raw = repl.get(rec.start)
        if raw is None:
            return False
        return _pad_byte(raw, rec.start not in objective) is not None

    def pick(records, i):
        """i 번 레코드 앞쪽에서 손대도 되는(번역된) 레코드를 찾는다.

        상자에 여유가 있는 레코드를 먼저 고른다 — 여유가 없는 레코드를 늘리면
        그 줄이 상자를 한 칸 넘어 화면에서 글자가 잘린다.
        """
        for want_room in (True, False):
            for j in range(i, -1, -1):
                if records[j].start in safe and (not want_room or _has_room(records[j])):
                    return records[j]
        # 앞쪽이 전부 스크립트 레코드인 경우가 있다(EX 시나리오 8/23/52/55: 레코드 0
        # 이 스크립트인데 그 안의 포인터 변위에 0xFF 가 들어 레코드가 거기서 끊긴다).
        # 그럴 땐 **뒤쪽** 번역 레코드를 늘려도 된다 — 뒤를 밀면 그 포인터가 겨누는
        # 타깃이 움직여서 변위가 바뀌고, 경계가 원문과 같아질 수 있다.
        for want_room in (True, False):
            for j in range(i + 1, len(records)):
                if records[j].start in safe and (not want_room or _has_room(records[j])):
                    return records[j]
        return None
    for _ in range(rounds):
        out, _meta = rebuild(src, repl)
        fixed, _, _ = retarget(out, src, apply=True, verbose=False)
        sj = parse_scenarios(src)
        sk = parse_scenarios(fixed)
        grow = []
        pre = parse_scenarios(out)      # 재조준 전 (경계가 레트일과 같은 상태)
        for a, b, c in zip(sj, sk, pre):
            if len(a.records) == len(b.records) or a.index in unsafe:
                continue
            # 어긋남을 만든 포인터의 **타깃**을 찾아, 그 바로 앞 레코드를 늘린다.
            # 레코드 0 처럼 포인터가 스무 개씩 든 스크립트 레코드를 통째로 늘리면
            # 그 안의 변위가 한꺼번에 움직여 서로를 깨뜨려 영영 안 맞는다.
            starts = [r.start for r in c.records]
            picked = False
            for tgt in sorted(_bad_operand_targets(fixed, c)):
                if tgt in starts:
                    i = starts.index(tgt)
                    if i:
                        # 바로 앞 레코드에 여유가 없으면 더 앞에서 여유 있는 걸 고른다.
                        # 여유 없는 레코드를 늘리면 그 줄이 상자를 넘어 깨진다.
                        rec = a.records[i - 1]
                        if not _has_room(rec):
                            rec = pick(a.records, i - 1) or rec
                        grow.append(rec)
                        picked = True
            if picked:
                continue
            k = next((i for i, (x, y) in enumerate(zip(a.records, b.records))
                      if (x.end - x.start) != (y.end - y.start)), 0)
            back = stuck.get(a.index, 0)
            stuck[a.index] = back + 1
            rec = pick(a.records, max(k - 1 - back, 0))
            if rec is None:
                # 스크립트 레코드밖에 없다 — 여기서 멈추는 게 낫다. 경계가 어긋난
                # 채로 두면 그 시나리오의 조건문 표시만 어긋나지만, 스크립트에
                # 바이트를 끼우면 게임이 멈춘다.
                unsafe.add(a.index)
                continue
            grow.append(rec)
        if not grow:
            stuck.clear()
            if verbose:
                note = f", 손댈 수 없어 남긴 시나리오 {sorted(unsafe)}" if unsafe else ""
                print(f"  레코드 경계 안정화: 앞 레코드 늘림 {added}개{note}")
            return repl, out
        grew = False
        for rec in grow:
            cur = repl.get(rec.start) or bytes(src[rec.start:rec.end])
            pad = _pad_byte(cur, rec.start not in objective)
            if pad is None:
                # 여유가 없는 레코드다. 예전엔 `or bytes(1)` 로 그냥 공백을 붙였는데
                # 그러면 그 줄이 상자를 한 칸 넘어 화면에서 글자가 잘린다
                # (EX 0xA73C2 에서 적발). 손대지 않고 넘어간다.
                continue
            repl[rec.start] = cur[:-1] + pad + cur[-1:]
            added += 1
            grew = True
        if not grew:
            # 늘릴 수 있는 레코드가 하나도 없다. 화면을 깨뜨리면서까지 맞추지는
            # 않는다 — 경계가 어긋난 시나리오는 작전목적 조건문 표시만 어긋난다.
            for a, b, c in zip(sj, sk, pre):
                if len(a.records) != len(b.records):
                    unsafe.add(a.index)
            stuck.clear()
            if verbose:
                print(f"  레코드 경계 안정화: 앞 레코드 늘림 {added}개, "
                      f"손댈 수 없어 남긴 시나리오 {sorted(unsafe)}")
            return repl, out
    if verbose:
        print(f"  레코드 경계 안정화: {rounds}회 안에 다 맞추지 못했습니다 "
              f"(늘림 {added}개, 남은 시나리오 {sorted(unsafe)})")
    return repl, out


def nudge_ff_operands(ko_bytes, jp_bytes, *, rounds=8, verbose=True):
    """0xFF 변위가 생긴 타깃을 한 바이트 뒤로 밀어 재조준한다.

    앞 레코드의 종결자 바로 앞에 0x00(반각 공백)을 하나 끼우면 타깃이 1바이트
    뒤로 가고 변위가 달라진다. 줄 끝 공백이라 화면에는 표가 안 난다.
    """
    ko = bytearray(ko_bytes)
    moved = 0
    for _ in range(rounds):
        ko = bytearray(retarget(bytes(ko), jp_bytes, apply=True, verbose=False)[0])
        scns = parse_scenarios(bytes(ko))
        todo = []
        for scn in scns:
            for tgt in _bad_operand_targets(ko, scn):
                if scn.record_data_end + 1 > scn.pool_end:
                    continue                      # 풀에 여유가 없다
                todo.append((scn, tgt))
        if not todo:
            break
        # 뒤에서부터 끼워 넣어야 앞쪽 오프셋이 안 흔들린다
        for scn, tgt in sorted(todo, key=lambda x: -x[1]):
            ko[tgt:tgt] = bytes(1)
            del ko[scn.pool_end - 1]              # 풀 꼬리 패딩 한 바이트를 뺀다
            moved += 1
    ko = bytearray(retarget(bytes(ko), jp_bytes, apply=True, verbose=False)[0])
    left = sum(len(_bad_operand_targets(ko, s)) for s in parse_scenarios(bytes(ko)))
    if verbose:
        print(f"  0xFF 변위 회피: {moved}곳 밀어냄, 남은 것 {left}")
    return bytes(ko), moved, left


def _verify(ko_bytes, jp_bytes):
    """재조준 후: 모든 이벤트 참조가 배포본 레코드 시작을 가리키는지."""
    bj = parse_scenarios(jp_bytes)
    bk = parse_scenarios(ko_bytes)
    bad = 0
    for si in range(len(bj)):
        sj, sk = bj[si], bk[si]
        starts = {r.start for r in sk.records}
        for off_j, op, tgt_j in scan_pool_refs(jp_bytes, sj):
            host = record_index_containing(sj, off_j)
            off_in_rec = off_j - sj.records[host].start
            off_k = sk.records[host].start + off_in_rec
            if off_k + 4 > len(ko_bytes) or ko_bytes[off_k] != op:
                continue          # 우연 매치(텍스트 재번역으로 밀림) — 검증 대상 아님
            opnd_k = operand_offset(ko_bytes, off_k)
            if opnd_k is None:
                continue
            disp = struct.unpack_from("<h", ko_bytes, opnd_k)[0]
            if opnd_k + disp not in starts:
                bad += 1
    return bad


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--ko", required=True)
    ap.add_argument("--jp", default=f"{R}/extracted/SECOND/2_SCE.BIN")
    ap.add_argument("--out")
    a = ap.parse_args()
    ko = open(a.ko, "rb").read()
    jp = open(a.jp, "rb").read()
    print("[재조준 시뮬 (기록 안 함)]")
    _, need, probs = retarget(ko, jp, apply=False)
    if a.out and not probs:
        print("[재조준 적용]")
        fixed, _, _ = retarget(ko, jp, apply=True)
        bad = _verify(fixed, jp)
        print(f"  검증: 재조준 후 스테일 참조 {bad}건")
        if bad == 0:
            open(a.out, "wb").write(fixed)
            print(f"  WROTE {a.out}")
