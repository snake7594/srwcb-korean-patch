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
if str(_P.TOOLS) not in _sys.path:
    _sys.path.insert(0, str(_P.TOOLS))
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
