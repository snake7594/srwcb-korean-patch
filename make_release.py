# -*- coding: utf-8 -*-
"""릴리스 아티팩트를 만들고 저장소 소스를 함께 갱신한다.

릴리스를 낼 때마다 **패치 파일만 올리고 소스는 뒤처지는** 일이 없도록, 이 한
스크립트가 둘 다 한다.

    python make_release.py v0.10.9 --prev v0.10.8

하는 일
    1. 저장소 소스가 실제 빌드에 쓰인 것과 같은지 확인(뒤처짐 감지)
    2. CB·단독판 xdelta 생성 + 역적용 검증
    3. .cue / easy-apply zip / SHA256SUMS 생성
    4. 릴리스 노트 초안 생성(없으면)

게임 이미지는 저장소에 넣지 않는다. xdelta3 는 별도로 준비해야 한다.
"""
import argparse
import hashlib
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import srwcb_paths as P  # noqa: E402

XDELTA = Path(os.environ.get("SRWCB_XDELTA", P.WORK / "xdelta.exe"))


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def xdelta(src: Path, dst: Path, out: Path) -> Path:
    subprocess.run([str(XDELTA), "-e", "-9", "-S", "none", "-f",
                    "-s", str(src), str(dst), str(out)], check=True)
    return out


def verify(src: Path, patch: Path, dst: Path) -> bool:
    tmp = P.WORK / ("_verify_" + patch.name + ".bin")
    if tmp.exists():
        tmp.unlink()
    subprocess.run([str(XDELTA), "-d", "-s", str(src), str(patch), str(tmp)], check=True)
    ok = sha(tmp) == sha(dst)
    tmp.unlink()
    return ok


def cue(path: Path, track1: str, track2: str | None = None) -> None:
    lines = [f'FILE "{track1}" BINARY', "  TRACK 01 MODE2/2352", "    INDEX 01 00:00:00"]
    if track2:
        lines += [f'FILE "{track2}" BINARY', "  TRACK 02 AUDIO",
                  "    INDEX 00 00:00:00", "    INDEX 01 00:02:00"]
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def check_sources_current() -> None:
    """저장소 스크립트에 하드코딩 경로가 남았는지 — 재현성 회귀 감지."""
    bad = []
    for f in REPO.rglob("*.py"):
        if "__pycache__" in str(f) or f.name == "srwcb_paths.py":
            continue
        t = f.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"[A-Z]:/(?:ps1|Users)/", t):
            bad.append(f.relative_to(REPO))
    if bad:
        raise SystemExit(
            "[재현성 회귀] 하드코딩된 절대경로가 있습니다. srwcb_paths 를 쓰세요:\n  "
            + "\n  ".join(str(x) for x in bad))
    print("  소스 이식성 확인: 하드코딩 경로 없음")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="예: v0.10.9")
    ap.add_argument("--prev", required=True, help="직전 버전 (문구 치환용)")
    ap.add_argument("--cb-image", type=Path, help="완성된 CB Track1 (기본 work/out)")
    ap.add_argument("--skip-standalone", action="store_true")
    a = ap.parse_args()
    VER, PREV = a.version, a.prev
    REL = REPO / "release"
    REL.mkdir(exist_ok=True)

    print("[1/4] 소스 상태 확인")
    check_sources_current()

    cb_img = a.cb_image or (P.OUT / f"Super Robot Taisen Complete Box Korean {VER} (Track 1).bin")
    if not cb_img.exists():
        raise SystemExit(f"[없음] 완성 이미지: {cb_img}\n  먼저 build_all.py 로 빌드하세요.")
    if not XDELTA.exists():
        raise SystemExit(f"[없음] xdelta3: {XDELTA}\n  SRWCB_XDELTA 로 지정하세요.")

    print("\n[2/4] CB 패치 생성")
    disc = P.disc()
    cbp = xdelta(disc, cb_img, REL / f"srwcb-second-third-ex-korean-{VER}.xdelta")
    if not verify(disc, cbp, cb_img):
        raise SystemExit("[실패] CB xdelta 역적용 불일치")
    cbc = REL / f"srwcb-cb-korean-{VER}.cue"
    cue(cbc, cb_img.name, "Super Robot Taisen Complete Box (Track 2).bin")
    print(f"  {cbp.name} {cbp.stat().st_size:,}B  역적용 OK")
    assets = [cbp, cbc]

    if not a.skip_standalone:
        print("\n[3/4] 단독판 패치")
        for key, src, out_img, name in (
            ("srw2", P.SRW2_IMG, P.WORK / "srw2" / "port" / "Super Robot Taisen 2 (Korean).img",
             "srw2-standalone-korean"),
            ("srw3", P.SRW3_BIN, P.WORK / "srw3" / "port" / "Dai 3 Ji Super Robot Taisen (Korean).bin",
             "srw3-standalone-korean"),
            ("srwex", P.SRWEX_IMG, P.WORK / "srwex" / "port" / "Super Robot Taisen EX (Korean).img",
             "srwex-standalone-korean"),
        ):
            if not (src and src.exists() and out_img.exists()):
                print(f"  {key}: 건너뜀 (원본 또는 빌드 결과 없음)")
                continue
            p = xdelta(src, out_img, REL / f"{name}-{VER}.xdelta")
            if not verify(src, p, out_img):
                raise SystemExit(f"[실패] {key} 역적용 불일치")
            c = REL / f"{name}-{VER}.cue"
            cue(c, out_img.name)
            assets += [p, c]
            print(f"  {name}: {p.stat().st_size:,}B  역적용 OK")
    else:
        print("\n[3/4] 단독판 건너뜀")

    print("\n[4/4] easy-apply + 체크섬")
    for sub, patch, cuef, img, zipname in (
        ("easy-apply", cbp, cbc, cb_img, f"srwcb-cb-korean-{VER}-easy-apply.zip"),
    ):
        d = REPO / sub
        ps = d / "apply.ps1"
        if ps.exists():
            raw = ps.read_bytes(); bom = raw[:3] == b"\xef\xbb\xbf"
            t = raw.decode("utf-8-sig").replace(PREV, VER)
            t = re.sub(r"(\$EXP_OUT\s*=\s*')[0-9a-f]{64}(')", r"\g<1>" + sha(img) + r"\2", t)
            t = re.sub(r"(\$EXP_PATCH\s*=\s*')[0-9a-f]{64}(')", r"\g<1>" + sha(patch) + r"\2", t)
            ps.write_bytes((b"\xef\xbb\xbf" if bom else b"") + t.encode("utf-8"))
        out = REL / zipname
        if out.exists():
            out.unlink()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for f in sorted(d.iterdir()):
                if f.is_file():
                    z.write(f, f.name)
            z.write(patch, patch.name); z.write(cuef, cuef.name)
            if XDELTA.exists():
                z.write(XDELTA, "xdelta.exe")
        assets.append(out)
        print(f"  {zipname} {out.stat().st_size:,}B")

    sums = REL / f"SHA256SUMS_{VER}.txt"
    with open(sums, "w", encoding="utf-8") as f:
        for x in assets:
            f.write(f"{sha(x)}  {x.name}\n")
        f.write(f"\n# 패치 결과 이미지\n{sha(cb_img)}  {cb_img.name}\n")
    print(f"  {sums.name}")

    notes = REPO / "docs" / f"RELEASE_NOTES_{VER}.md"
    if not notes.exists():
        notes.write_text(f"# {VER}\n\n(내용을 채우세요 — 무엇이 왜 바뀌었는지)\n",
                         encoding="utf-8")
        print(f"\n  릴리스 노트 초안: {notes.relative_to(REPO)}")

    print(f"\n완료. 다음:\n"
          f"  1) docs/RELEASE_NOTES_{VER}.md 작성\n"
          f"  2) README.md 최신 버전 문구 갱신\n"
          f"  3) git commit && git tag {VER} && git push\n"
          f"  4) gh release create {VER} --notes-file … release/*{VER}*")


if __name__ == "__main__":
    main()
