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
from analyze_sce_relocation import parse_scenarios, TEXT_POINTER_OPCODES


def scan_pool_refs(buf, scn):
    """pool 레코드 영역에서 target 이 레코드 시작인 B1/B3/B4 (진짜 이벤트 참조)."""
    starts = {r.start for r in scn.records}
    refs = []
    for off in range(scn.pool_start, scn.record_data_end - 2):
        op = buf[off]
        if op not in TEXT_POINTER_OPCODES:
            continue
        disp = struct.unpack_from("<H", buf, off + 1)[0]
        tgt = (off + 1) + disp
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
            new_disp = new_tgt - (off_k + 1)
            if not (0 <= new_disp <= 0xFFFF):
                problems.append(f"sc{si} @{off_k:#x}: 변위 범위초과 {new_disp:#x}")
                continue
            cur_disp = struct.unpack_from("<H", ko, off_k + 1)[0]
            cur_tgt = (off_k + 1) + cur_disp
            if cur_tgt == new_tgt:
                already += 1
                continue
            if apply:
                struct.pack_into("<H", ko, off_k + 1, new_disp)
            fixed += 1
    if verbose:
        print(f"  이벤트 참조 총 {total}  재조준필요 {fixed}  이미정상 {already}  "
              f"우연매치(스킵) {spurious}  문제 {len(problems)}")
        for s in spurious_list[:8]:
            print("   ~ 우연매치 스킵:", s)
        for p in problems[:20]:
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
    for off in range(scn.pool_start, scn.record_data_end - 2):
        if buf[off] not in TEXT_POINTER_OPCODES:
            continue
        if 0xFF not in buf[off + 1:off + 3]:
            continue
        tgt = (off + 1) + struct.unpack_from("<H", buf, off + 1)[0]
        if tgt in starts:
            out.add(tgt)
    return out


def harden_against_ff_operands(src, replacements, rebuild, *, rounds=60, verbose=True):
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

    def pick(records, i):
        """i 번 레코드 앞쪽에서 손대도 되는(번역된) 레코드를 찾는다."""
        for j in range(i, -1, -1):
            if records[j].start in safe:
                return records[j]
        return None          # 안 풀리는 시나리오는 더 앞 레코드를 늘려 본다
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
                        grow.append(a.records[i - 1])
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
        for rec in grow:
            cur = repl.get(rec.start) or bytes(src[rec.start:rec.end])
            repl[rec.start] = cur[:-1] + bytes(1) + cur[-1:]
            added += 1
    raise SystemExit("레코드 경계를 레트일과 맞추지 못했습니다")


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
            if off_k + 3 > len(ko_bytes) or ko_bytes[off_k] != op:
                continue          # 우연 매치(텍스트 재번역으로 밀림) — 검증 대상 아님
            disp = struct.unpack_from("<H", ko_bytes, off_k + 1)[0]
            if (off_k + 1) + disp not in starts:
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
