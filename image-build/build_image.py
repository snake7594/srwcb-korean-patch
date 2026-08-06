# -*- coding: utf-8 -*-
"""빌드된 조각들을 모아 후처리를 얹고 최종 이미지를 만든다 (build_all 7단계).

3~6단계가 만든 것들은 아직 '주입 직후' 상태다. 여기서 그동안 따로 돌리던 교정을
전부 순서대로 적용한 뒤 레트일 위에 한 번에 조립한다.

    1. 제2차 이미지에서 SECOND.WAR / SLPS_020.70 을 꺼낸다
    2. 종료(전원끄기) 메시지 한글 주입 — SECOND.WAR / THIRD.WAR
    3. 이벤트 스크립트 포인터 재조준 — 2_SCE / 3_SCE / E_SCE
       (안 하면 브리핑에서 멈춘다)
    4. 전투/사망 대사 줄바꿈 재정렬 — BMESS2/3/4, *_DEAD (실측 폭 29)
    5. 메뉴 칸 정렬 교정 — THIRD.WAR / EX.WAR / TR.WAR (제2차 기준)
    6. 잔여 미번역 UI 보충 — TR 은 EX 에서 이식, 나머지는 도너 재배치
    7. 게임 선택 화면 그래픽(C_SMAP) 한글판
    8. 레트일 + 이 19개 파일로 이미지 조립

각 단계는 크기를 안 바꾸거나(제자리·도너) 조립기가 위치를 다시 잡아 주므로
서로 간섭하지 않는다.
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
for _sub in ("tools", "tools/graphics", "third-ui", "ex-ui", "tr-ui", "audit",
             "menu-align", "second-fixes", "image-build"):
    _p = _os.path.join(_d, *_sub.split("/"))
    if _os.path.isdir(_p) and _p not in _sys.path:
        _sys.path.append(_p)
# ------------------------------------------------------------------
import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(_P.TOOLS))
from extract_psx_iso import RawMode2Image, read_tree  # noqa: E402
import assemble_image as AI  # noqa: E402

W = _P.WORK
SECOND_IMG = (_P.BUILD / "second_korean_v0.8.7-full-menus" /
              "Super Robot Taisen Complete Box Second Korean v0.8.7-full-menus (Track 1).bin")
TH = W / "test_build" / "third_full"
EX = W / "test_build" / "ex_full"
TR = W / "test_build" / "tr_full"
S2 = _P.BUILD / "second_korean_v0.8.7-full-menus"

RAW = {
    "BMESS2.BIN": S2 / "rebuilt" / "BMESS2.BIN",
    "SECOND/2_SCE.BIN": S2 / "rebuilt" / "SECOND" / "2_SCE.BIN",
    "SECOND/2_DEAD.BIN": S2 / "rebuilt" / "SECOND" / "2_DEAD.BIN",
    "BMESS3.BIN": TH / "rebuilt" / "BMESS3.BIN",
    "THIRD/3_SCE.BIN": TH / "rebuilt" / "THIRD" / "3_SCE.BIN",
    "THIRD/3_DEAD.BIN": TH / "rebuilt" / "THIRD" / "3_DEAD.BIN",
    "THIRD/THIRD.WAR": TH / "runtime" / "THIRD" / "THIRD.WAR",
    "BMESS4.BIN": EX / "rebuilt" / "BMESS4.BIN",
    "EX/E_SCE.BIN": EX / "rebuilt" / "EX" / "E_SCE.BIN",
    "EX/E_DEAD.BIN": EX / "rebuilt" / "EX" / "E_DEAD.BIN",
    "EX/EX.WAR": EX / "runtime" / "EX" / "EX.WAR",
    "TR.WAR": TR / "TR_final.war",
}
RETAIL = {
    "SECOND/2_SCE.BIN": _P.EXTRACTED / "SECOND" / "2_SCE.BIN",
    "THIRD/3_SCE.BIN": _P.EXTRACTED / "THIRD" / "3_SCE.BIN",
    "EX/E_SCE.BIN": _P.EXTRACTED / "EX" / "E_SCE.BIN",
}
BMESS = ["BMESS2.BIN", "BMESS3.BIN", "BMESS4.BIN"]
DEAD = ["SECOND/2_DEAD.BIN", "THIRD/3_DEAD.BIN", "EX/E_DEAD.BIN"]
CSMAP = _P.BUILD / "gfx" / "C_SMAP_ko.BIN"


def need(p: Path, what: str) -> Path:
    if not p.exists():
        raise SystemExit(f"[없음] {what}: {p}\n  먼저 build_all.py 3~6단계를 돌리세요.")
    return p


def from_image(img: Path, name: str) -> bytes:
    with RawMode2Image(img) as m:
        _, E = read_tree(m)
    e = next(x for x in E if x.path.strip("/") == name)
    return AI.read_file(img, e.lba, e.size)


def collect() -> dict:
    files = {k: need(v, k).read_bytes() for k, v in RAW.items()}
    need(SECOND_IMG, "제2차 빌드 이미지")
    for n in ("SECOND/SECOND.WAR", "SLPS_020.70"):
        files[n] = from_image(SECOND_IMG, n)
    return files


def step_quit(files):
    import inject_quit as Q
    for name, game in (("SECOND/SECOND.WAR", "second"), ("THIRD/THIRD.WAR", "third")):
        out, rep = Q.inject(files[name], game, verbose=False)
        # 앵커는 일본어 원문이다. 주입기가 이미 한글 풀을 심어 둔 경우(제3차)에는
        # 하나도 안 잡히는데, 그건 실패가 아니라 '이미 됨' 이다.
        gone = [r for r in rep if str(r[1]).startswith("FOUND=")]
        if len(gone) == len(rep):
            print(f"  {name}: 이미 한글 (건너뜀)")
            continue
        miss = [r for r in rep if not r[-1]]
        if miss:
            raise SystemExit(f"{name}: 종료 메시지 주입 실패 {len(miss)}건")
        files[name] = out
        print(f"  {name}: 종료 메시지 {len(rep)}개 주입")


def _same_record_split(ko: bytes, jp: bytes, name: str) -> None:
    """레코드 경계가 레트일과 같은지.

    B1/B3/B4 는 뒤에 2바이트 피연산자를 달고 다니는데, 레코드를 훑는 문법은 그걸
    모른다. 재조준으로 그 피연산자에 0xFF 가 생기면 거기서 레코드가 끊긴 것처럼
    보이고, 그 뒤 레코드 번호가 통째로 밀려 조건문·대사가 엉뚱하게 나온다.
    """
    from analyze_sce_relocation import parse_scenarios
    a, b = parse_scenarios(jp), parse_scenarios(ko)
    bad = [i for i, (x, y) in enumerate(zip(a, b)) if len(x.records) != len(y.records)]
    if bad:
        raise SystemExit(
            f"{name}: 레코드 경계가 레트일과 달라진 시나리오 {bad}\n"
            f"  재조준된 포인터 피연산자에 0xFF 가 생겼을 수 있습니다.")


def step_sce(files):
    import fix_sce_event_refs as FX
    for name, jp_path in RETAIL.items():
        jp = need(jp_path, name).read_bytes()
        _same_record_split(files[name], jp, name)
        _, need_n, probs = FX.retarget(files[name], jp, apply=False, verbose=False)
        if probs:
            raise SystemExit(f"{name}: 재조준 불가 {len(probs)}건")
        fixed, _, _ = FX.retarget(files[name], jp, apply=True, verbose=False)
        bad = FX._verify(fixed, jp)
        if bad:
            raise SystemExit(f"{name}: 재조준 후에도 스테일 참조 {bad}건")
        files[name] = fixed
        _same_record_split(files[name], jp, name)
        print(f"  {name}: 이벤트 참조 재조준 {need_n}곳")


def step_battle(files):
    import fix_battle_linebreaks as FB
    for name in BMESS:
        fixed, recs, rm, over = FB.fix_bmess(files[name])
        if over:
            raise SystemExit(f"{name}: 재래핑 후에도 폭 29 초과 {len(over)}건")
        files[name] = fixed
        print(f"  {name}: 레코드 {recs:,} / 잘못된 줄바꿈 {rm:,}B 제거")
    for name in DEAD:
        fixed, recs, rm, over = FB.fix_dead(files[name])
        if over:
            raise SystemExit(f"{name}: 재래핑 후에도 폭 29 초과 {len(over)}건")
        files[name] = fixed
        print(f"  {name}: 레코드 {recs:,} / {rm:,}B 제거")


def step_menu(files):
    import second_ui_transplant as ST
    import menu_align_fix as MA
    ST.SECOND_PATCHED = files["SECOND/SECOND.WAR"]
    cache = Path(ST.CACHE)
    if cache.exists():
        cache.unlink()          # 제2차 패치본이 바뀌면 캐시도 다시 만든다
    MA.SOURCES = {"THIRD.WAR": files["THIRD/THIRD.WAR"],
                  "EX.WAR": files["EX/EX.WAR"],
                  "TR.WAR": files["TR.WAR"]}
    fixed = MA.main()
    for key, iso in (("THIRD", "THIRD/THIRD.WAR"), ("EX", "EX/EX.WAR"), ("TR", "TR.WAR")):
        files[iso] = fixed[key]


def step_third_ui(files):
    """제3차에 남아 있던 UI 잔재 (제보 #5)."""
    import fix_third_ui_leftovers as F3
    out, n, menu = F3.apply(files["THIRD/THIRD.WAR"])
    files["THIRD/THIRD.WAR"] = out
    print(f"  한자 잔재 제자리 교체 {n}곳 / 맵 명령 메뉴: {menu}")


def step_leftover(files):
    """전면 재검증에서 찾은 잔여 미번역 UI 보충."""
    import audit_leftover as AL
    n = AL.apply(files)
    print(f"  잔여 미번역 UI {n}건 보충")


def step_csmap(files):
    if not CSMAP.exists():
        print("  게임 선택 화면 그래픽 생성")
        subprocess.run([sys.executable, str(_P.REPO / "tools" / "graphics" / "build_csmap_ko.py")],
                       check=True)
    files["C_SMAP.BIN"] = need(CSMAP, "한글 C_SMAP").read_bytes()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", default="v0.11.0")
    ap.add_argument("--out", type=Path)
    ap.add_argument("--skip-leftover", action="store_true")
    a = ap.parse_args()

    print("[1/8] 빌드 결과 수집")
    files = collect()
    print("[2/8] 종료 메시지")
    step_quit(files)
    print("[3/8] 이벤트 스크립트 포인터 재조준")
    step_sce(files)
    print("[4/8] 전투·사망 대사 줄바꿈")
    step_battle(files)
    print("[5/8] 메뉴 칸 정렬")
    step_menu(files)
    print("[6/8] 잔여 미번역 UI")
    step_third_ui(files)
    if a.skip_leftover:
        print("  건너뜀")
    else:
        step_leftover(files)
    print("[7/8] 게임 선택 화면 그래픽")
    step_csmap(files)

    fin = _P.BUILD / "final"
    fin.mkdir(parents=True, exist_ok=True)
    for k, v in files.items():
        q = fin / k.replace("/", "_")
        q.write_bytes(v)
    print(f"  최종 파일 {len(files)}개 -> {fin}")

    print("[8/8] 이미지 조립")
    out = a.out or (_P.OUT / f"Super Robot Taisen Complete Box Korean {a.version} (Track 1).bin")
    out.parent.mkdir(parents=True, exist_ok=True)
    AI.assemble(_P.disc(), out, files)
    cue = AI.write_cue(out)
    print(f"\nOUT {out}\n    {cue.name}")
    print("\nTrack 2 는 원본 디스크의 것을 같은 폴더에 "
          '"Super Robot Taisen Complete Box (Track 2).bin" 이름으로 두세요.')


if __name__ == "__main__":
    main()
