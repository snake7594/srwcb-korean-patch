# -*- coding: utf-8 -*-
"""한글 패치를 처음부터 끝까지 만든다 — 2단계.

`setup_workspace.py` 로 작업 폴더를 준비한 뒤 실행한다.

    python setup_workspace.py --disc "…(Track 1).bin"
    python build_all.py

하는 일

    1. 원문 대사 후보 추출        게임에서 뽑은 파일 → dialogue_japanese.full.json
    2. 원장(ledger) 생성          제2차 / 제3차 / EX 각각
    3. 제2차 빌드                 폰트 + 대사/UI 주입 → 데이터·실행파일
    4. 제3차 빌드
    5. EX 빌드
    6. 트레이닝 모드 빌드
    7. 후처리 + 디스크 이미지 조립
    8. 검증(감사)
    9. 단독판 3종 (별매 CD 를 가진 사람만; setup_standalone.py 로 준비)

원장은 게임의 일본어 원문이라 저장소에 넣지 않는다. 각자 자기 디스크에서
1~2단계로 만든다. 한국어 번역·도구는 전부 저장소에 있다.

`--from N` 으로 중간 단계부터 이어서 돌릴 수 있다(오래 걸리는 1~2단계 건너뛰기).
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import srwcb_paths as P  # noqa: E402

PY = sys.executable


def run(desc: str, args: list[str], cwd: Path | None = None) -> None:
    print(f"\n=== {desc}")
    t = time.time()
    r = subprocess.run([PY, *args], cwd=str(cwd or REPO))
    if r.returncode != 0:
        raise SystemExit(f"[실패] {desc} (종료코드 {r.returncode})")
    print(f"    ({time.time() - t:.0f}초)")


def need(p: Path, what: str) -> Path:
    if not p.exists():
        raise SystemExit(
            f"[없음] {what}: {p}\n"
            f"  먼저 `python setup_workspace.py --disc \"…(Track 1).bin\"` 를 실행하세요."
        )
    return p


STEPS = {
    1: "원문 대사 후보 추출",
    2: "번역 원장 생성 (제2차·제3차·EX)",
    3: "제2차 빌드",
    4: "제3차 빌드",
    5: "EX 빌드",
    6: "트레이닝 모드 빌드",
    7: "후처리 + 디스크 이미지 조립",
    8: "검증",
    9: "단독판 3종 (별매 CD, 선택)",
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", type=int, default=1,
                    help="이 단계부터 시작 (1~9)")
    ap.add_argument("--only", type=int, help="이 단계만 실행")
    ap.add_argument("--to", dest="stop", type=int, default=9,
                    help="이 단계까지만 실행 (1~9)")
    ap.add_argument("--version", default="v0.11.0", help="완성 이미지 이름에 쓸 버전")
    a = ap.parse_args()
    P.ensure_dirs()
    need(P.EXTRACTED / "SECOND" / "SECOND.WAR", "추출된 게임 파일")

    def want(n: int) -> bool:
        return a.only == n if a.only else a.start <= n <= a.stop

    print(f"작업 폴더: {P.WORK}")
    print("단계:", ", ".join(f"{k}.{v}" for k, v in STEPS.items()))

    cand = P.LEDGER / "dialogue_japanese.full.json"
    if want(1):
        run(STEPS[1], [str(REPO / "tools" / "extract_dialogue_candidates.py"),
                       str(P.EXTRACTED), "--glyph-map", str(P.FONT_MAPPING),
                       "--mapping-policy", "reviewed", "--output", str(cand)])
    if want(2):
        need(cand, "대사 후보")
        for t in ("export_second_translation_v2.py", "export_third_translation.py",
                  "export_ex_translation.py"):
            run(f"{STEPS[2]} — {t}", [str(REPO / "tools" / t)])
    if want(3):
        # xdelta 는 배포할 수 없는 외부 도구다. 있으면 패치 파일까지 만들고,
        # 없으면 이미지까지만 만든다(플레이에는 이미지만 있으면 된다).
        xd = P.WORK / "xdelta.exe"
        extra = [] if xd.exists() else ["--skip-xdelta"]
        if extra:
            print("  (xdelta.exe 없음 — 이미지만 만들고 .xdelta 생성은 건너뜁니다.\n"
                  f"   패치 파일도 원하면 xdelta3 를 {xd} 로 두세요)")
        run(STEPS[3], [str(REPO / "tools" / "build_second_expanded_patch.py"), *extra])
    if want(4):
        run(STEPS[4], [str(REPO / "third-ui" / "build_third_full.py")])
        run("제3차 UI 주입", [str(REPO / "third-ui" / "inject_third_ui.py")])
    if want(5):
        run(STEPS[5], [str(REPO / "ex-ui" / "build_ex_full.py")])
        run("EX UI 주입", [str(REPO / "ex-ui" / "inject_ex_ui.py")])
    if want(6):
        run("트레이닝 오프셋 검증", [str(REPO / "tr-ui" / "verify_tr_offsets.py")])
        run("트레이닝 UI 주입", [str(REPO / "tr-ui" / "inject_tr_ui.py")])
        run(STEPS[6], [str(REPO / "tr-ui" / "build_tr_full.py")])
    if want(7):
        run(STEPS[7], [str(REPO / "image-build" / "build_image.py"),
                       "--version", a.version])
    if want(8):
        run("글리프 무결성", [str(REPO / "tr-ui" / "verify_tr_glyphs.py")])
        run("최종 이미지 검증", [str(REPO / "audit" / "verify_image.py"),
                          "--version", a.version])
        run("이름 표기 사전 검증",
            [str(REPO / "audit" / "fix_name_spellings.py"), "--check"])
        run("세이브 머리글 정렬 검증",
            [str(REPO / "audit" / "verify_save_header.py"), "--version", a.version])
        run("목록 행 커서 왕복 검증",
            [str(REPO / "audit" / "verify_list_rows.py"), "--version", a.version])
        run("UI 런 폭·반칸 검증",
            [str(REPO / "audit" / "verify_ui_runs.py"), "--version", a.version])
        run("화자 검증",
            [str(REPO / "audit" / "verify_speakers.py"), "--version", a.version])
        run("VM 치환 루프 하한 검사 검증",
            [str(REPO / "audit" / "verify_vm_hardening.py"), "--version", a.version])
        run("전투 스크래치 검증",
            [str(REPO / "audit" / "verify_battle_scratch.py"), "--version", a.version])
        run("예고 타이틀 카드 검증(CB)",
            [str(REPO / "audit" / "verify_eyecatch.py"), "--cb-only"])
    if want(9):
        # 별매 CD 를 가진 사람만. 먼저 setup_standalone.py 로 준비한다.
        # 예고 타이틀 카드 그래픽은 네 디스크가 같은 파일을 쓴다 — 없으면 만든다.
        if not (P.BUILD / "gfx" / "EFFECT_ko.BIN").exists():
            run("예고 타이틀 카드 그래픽",
                [str(REPO / "tools" / "graphics" / "build_effect_ko.py")])
        for key, steps in (
            ("srw2", [("제2차 단독판", REPO / "standalone" / "build_standalone.py")]),
            ("srw3", [("제3차 단독판 실행파일", REPO / "standalone3" / "port_exe3.py"),
                      ("제3차 단독판 이미지", REPO / "standalone3" / "patch_iso3.py")]),
            ("srwex", [("EX 단독판 실행파일", REPO / "standalone_ex" / "port_exe_ex.py"),
                       ("EX 단독판 이미지", REPO / "standalone_ex" / "patch_iso_ex.py")]),
        ):
            if not (P.WORK / key / "extracted").exists():
                print(f"\n=== {key} 건너뜀 (setup_standalone.py 로 준비하세요)")
                continue
            for desc, script in steps:
                run(desc, [str(script)])
        run("단독판 검증", [str(REPO / "audit" / "verify_standalone.py")])
        run("예고 타이틀 카드 검증(단독판 포함)",
            [str(REPO / "audit" / "verify_eyecatch.py")])

    print(f"\n완료. 산출물: {P.OUT}")


if __name__ == "__main__":
    main()
