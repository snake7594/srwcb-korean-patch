# -*- coding: utf-8 -*-
"""빌드 작업 폴더를 준비한다 — 1단계.

정품 디스크에서 필요한 파일을 뽑아 `work/extracted/` 에 놓고, 저장소 안의 번역
자산을 파이프라인이 기대하는 자리에 연결한다. 게임에서 나온 것은 저장소에 절대
넣지 않으므로, 이 단계는 각자 실행해야 한다.

    python setup_workspace.py --disc "…/Super Robot Taisen Complete Box (Track 1).bin"

환경변수 `SRWCB_DISC` 를 미리 지정했다면 --disc 는 생략해도 된다.
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import srwcb_paths as P  # noqa: E402

sys.path.insert(0, str(P.TOOLS))

#: 디스크에서 뽑아야 하는 파일 (ISO 경로 → 저장 이름)
NEEDED = [
    "SLPS_020.70", "TR.WAR",
    "SECOND/SECOND.WAR", "SECOND/2_SCE.BIN", "SECOND/2_DEAD.BIN",
    "THIRD/THIRD.WAR", "THIRD/3_SCE.BIN", "THIRD/3_DEAD.BIN",
    "EX/EX.WAR", "EX/E_SCE.BIN", "EX/E_DEAD.BIN",
    "BMESS2.BIN", "BMESS3.BIN", "BMESS4.BIN",
    "C_SMAP.BIN",
]

#: 저장소 번역 자산 → 파이프라인이 읽는 작업 폴더 위치
LINKS = [
    ("translation", "translation_v2"),
    ("font", "research"),
]


def extract(disc: Path) -> int:
    from extract_psx_iso import RawMode2Image, read_tree
    import math
    SEC, UDO, UDS = 2352, 24, 2048
    with RawMode2Image(disc) as m:
        _, entries = read_tree(m)
    by = {e.path.strip("/"): e for e in entries}
    got = 0
    for name in NEEDED:
        e = by.get(name)
        if e is None:
            print(f"  [없음] {name} — 디스크가 컴플리트 박스가 맞는지 확인하세요")
            continue
        out = P.EXTRACTED / name
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.exists() and out.stat().st_size == e.size:
            got += 1
            continue
        buf = bytearray()
        with open(disc, "rb") as f:
            for i in range(math.ceil(e.size / UDS)):
                f.seek((e.lba + i) * SEC)
                buf += f.read(SEC)[UDO:UDO + UDS]
        out.write_bytes(bytes(buf[:e.size]))
        print(f"  추출 {name}  ({e.size:,}B)")
        got += 1
    return got


def link_assets() -> None:
    """저장소 자산을 작업 폴더에 연결(심볼릭 링크, 안 되면 복사)."""
    for src_name, dst_name in LINKS:
        src = REPO / src_name
        dst = P.WORK / dst_name
        if dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.symlink(src, dst, target_is_directory=True)
            print(f"  링크 {dst_name} -> {src_name}")
        except OSError:
            shutil.copytree(src, dst)
            print(f"  복사 {dst_name} <- {src_name} (링크 권한 없음)")
    # 폰트 매핑은 research/ 아래 이름으로도 읽힌다
    rm = P.WORK / "research" / P.FONT_MAPPING.name
    if not rm.exists():
        rm.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(P.FONT_MAPPING, rm)


def build_safe_font() -> None:
    """게임 폰트에 한글을 심은 '안전 폰트'를 만든다.

    빌드 파이프라인이 여기서 나온 글리프 매핑(tsv)과 폰트 블롭을 기준으로
    동작한다. 게임 실행파일이 입력이므로 저장소에 넣지 않고 매번 만든다.
    """
    out = P.BUILD / "exe_font_safe_test"
    if (out / "font" / "srwcb_font_hangul_test_2816_16x16.bin").exists():
        print("  이미 있음 — 건너뜀")
        return
    out.mkdir(parents=True, exist_ok=True)
    # 빌더는 다섯 실행파일이 한 폴더에 있길 기대하고, **그 자리에서 폰트를 주입**한다.
    # 그래서 원본(레트일)은 따로 보관해 두어야 나중에 비교·트랙 생성에 쓸 수 있다.
    safe_ex = out / "extracted"
    EXES = ("SLPS_020.70", "TR.WAR", "SECOND/SECOND.WAR",
            "THIRD/THIRD.WAR", "EX/EX.WAR")
    for rel in EXES:
        dst = safe_ex / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(P.EXTRACTED / rel, dst)
    retail = out / "retail_extracted"
    for rel in EXES:
        dst = retail / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            shutil.copyfile(P.EXTRACTED / rel, dst)
    import subprocess
    r = subprocess.run([sys.executable, str(REPO / "tools" / "build_exe_hangul_font.py"),
                        str(P.GALMURI_BDF), str(safe_ex), str(out)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:]); print(r.stderr[-1500:])
        raise SystemExit("[실패] 안전 폰트 생성")
    print(f"  생성: {out / 'font'}")


def build_safe_track(disc: Path) -> None:
    """한글 폰트만 주입한 기준 트랙을 만든다.

    이후 단계가 이 트랙 위에 번역 데이터를 얹는다. 원본 디스크가 입력이라
    저장소에 넣을 수 없고, 여기서 매번 만든다.
    """
    out = P.BUILD / "exe_font_safe_test"
    track = out / "Super Robot Taisen Complete Box Hangul Safe Font Test (Track 1).bin"
    if track.exists():
        print("  이미 있음 — 건너뜀")
        return
    retail = out / "retail_extracted"
    patched = out / "extracted"          # 폰트 빌더가 제자리 주입한 결과
    if not (retail.exists() and patched.exists()):
        raise SystemExit("[없음] 폰트 단계 산출물 — 안전 폰트 단계를 먼저 실행하세요")
    import subprocess
    r = subprocess.run([sys.executable, str(REPO / "tools" / "patch_raw_track_exes.py"),
                        str(disc), str(track), str(retail), str(patched)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:]); print(r.stderr[-1500:])
        raise SystemExit("[실패] 기준 트랙 생성")
    print(f"  생성: {track.name} ({track.stat().st_size:,}B)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disc", help="컴플리트 박스 Track 1 .bin")
    a = ap.parse_args()
    P.ensure_dirs()
    disc = Path(a.disc).expanduser().resolve() if a.disc else P.disc()
    if not disc.exists():
        raise SystemExit(f"[없음] 디스크 이미지: {disc}")
    print(f"작업 폴더: {P.WORK}")
    print(f"디스크    : {disc}\n")
    print("[1/5] 원본 파일 추출")
    n = extract(disc)
    print(f"  {n}/{len(NEEDED)} 개 준비됨\n")
    print("[2/5] 번역 자산 연결")
    link_assets()
    print("\n[3/5] 한글 폰트 생성")
    build_safe_font()
    print("\n[4/5] 기준 트랙 생성 (폰트만 주입)")
    build_safe_track(disc)
    print("\n[5/5] 실행파일 UI 인벤토리 생성")
    import subprocess
    inv = P.WORK / "research" / "second_exe_ui_full_inventory.json"
    if inv.exists():
        print("  이미 있음 — 건너뜀")
    else:
        r = subprocess.run([sys.executable,
                            str(REPO / "tools" / "analyze_second_exe_ui_full.py")],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stdout[-1200:]); print(r.stderr[-1200:])
            raise SystemExit("[실패] UI 인벤토리 생성")
        print("  " + r.stdout.strip().splitlines()[-1])
    print(f"\n완료. 다음: python build_all.py")


if __name__ == "__main__":
    main()
