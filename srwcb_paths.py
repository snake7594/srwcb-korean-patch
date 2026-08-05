# -*- coding: utf-8 -*-
"""저장소 어디서 실행하든 같은 경로를 쓰게 해 주는 공용 설정.

이 저장소는 원래 한 사람의 작업 폴더(`D:/ps1/roms/...`)에 맞춰 절대경로로
쓰여 있었다. 누구나 재현할 수 있도록 모든 경로를 여기로 모았다.

기본값은 **저장소 안**이다. 게임에서 뽑은 파일과 빌드 산출물은 저장소에 넣지
않으므로(배포 금지), 아래 두 곳만 각자 환경에 맞춰 두면 된다.

    work/            빌드 작업 폴더 (추출물·중간 산출물·완성 이미지)
    work/extracted/  정품 디스크에서 추출한 원본 파일

환경변수로 덮어쓸 수 있다:

    SRWCB_WORK      작업 폴더            (기본 <repo>/work)
    SRWCB_DISC      컴플리트 박스 Track 1 .bin 경로
    SRWCB_SRW2_IMG  제2차 단독판 .img
    SRWCB_SRW3_BIN  제3차 단독판 .bin
    SRWCB_SRWEX_IMG EX 단독판 .img

사용법:

    import srwcb_paths as P
    P.EXTRACTED / "SECOND" / "SECOND.WAR"
"""
from __future__ import annotations

import os
from pathlib import Path

REPO = Path(__file__).resolve().parent


def _env_path(name: str, default: Path | None) -> Path | None:
    v = os.environ.get(name)
    if v:
        return Path(v).expanduser().resolve()
    return default


# --- 저장소 안 (배포되는 자산) ---
TOOLS = REPO / "tools"
TRANSLATION = REPO / "translation"
FONT = REPO / "font"
RELEASE = REPO / "release"

#: 게임 폰트 글리프 ↔ 문자 매핑. 거의 모든 도구가 읽는다.
FONT_MAPPING = FONT / "srwcb_embedded_font_mapping_reviewed.json"
GALMURI_BDF = FONT / "Galmuri14.bdf"

# --- 작업 폴더 (배포하지 않음) ---
WORK = _env_path("SRWCB_WORK", REPO / "work")
EXTRACTED = WORK / "extracted"          # 정품 디스크에서 추출한 원본
LEDGER = WORK / "ledger"                # 원문 대사 원장 (직접 생성)
BUILD = WORK / "build"                  # 중간 산출물
OUT = WORK / "out"                      # 완성 이미지·패치

# --- 사용자 소유 원본 디스크 ---
DISC = _env_path("SRWCB_DISC", None)
SRW2_IMG = _env_path("SRWCB_SRW2_IMG", None)
SRW3_BIN = _env_path("SRWCB_SRW3_BIN", None)
SRWEX_IMG = _env_path("SRWCB_SRWEX_IMG", None)


def ensure_dirs() -> None:
    for d in (WORK, EXTRACTED, LEDGER, BUILD, OUT):
        d.mkdir(parents=True, exist_ok=True)


def require(path: Path | None, what: str) -> Path:
    """없으면 무엇을 어떻게 준비해야 하는지 알려 주고 멈춘다."""
    if path is None:
        raise SystemExit(
            f"[설정 필요] {what} 경로가 지정되지 않았습니다.\n"
            f"  환경변수로 지정하세요. 예)\n"
            f"    set SRWCB_DISC=D:\\games\\Super Robot Taisen Complete Box (Track 1).bin"
        )
    if not path.exists():
        raise SystemExit(f"[없음] {what}: {path}")
    return path


def disc() -> Path:
    return require(DISC, "컴플리트 박스 Track 1 .bin")


FINAL = BUILD / "final"


def final(iso_path: str, fallback: "Path | str | None" = None) -> Path:
    """7단계(build_image.py)가 남긴 **후처리까지 끝난** 파일.

    단독판 이식은 이걸 써야 한다. 주입 직후 파일(`rebuilt/`, `runtime/`)을 쓰면
    브리핑 프리즈·전투 대사 밀림·메뉴 정렬 같은 교정이 빠진 채로 이식된다.
    아직 7단계를 안 돌렸으면 fallback 을 쓴다.
    """
    p = FINAL / iso_path.replace("/", "_")
    if p.exists():
        return p
    if fallback is None:
        raise SystemExit(
            f"[없음] 후처리 결과 {p}\n"
            f"  먼저 `python build_all.py --only 7` 로 이미지 조립까지 돌리세요."
        )
    return Path(fallback)
