# 단독판(SLPS_024.06) 이식 빌드

컴플리트 박스판 v0.8.7 패치를 별매 단독판 실행파일 `SLPS_024.06`으로 이식하는
빌드 도구입니다. 검증된 patched `SECOND.WAR`(컴플리트 박스 빌드 산출물)를
delta-transplant + 포인터 의미별 재계산으로 옮깁니다.

## 재빌드 (컴플리트 박스 빌드 이후)

`config.py`의 경로(컴플리트 박스 빌드 디렉터리, SRW2 이미지 위치)를 환경에 맞게
설정한 뒤:

```bash
python build_standalone.py
```

단계: SRW2 retail 파일 추출 → delta 지도 → 실행파일 이식(port_exe) →
내용 검증(validate_content) → 이미지 패치 + .cue 생성(patch_iso).

산출물: `Super Robot Taisen 2 (Korean).img` + `.cue`.

## 파일

- `config.py` — 경로 설정(머신별로 조정)
- `extract_srw2.py` — 이미지에서 retail 파일 추출 + 음악 포인터 필드 유도
- `build_delta_map.py` — SECOND.WAR↔SLPS_024.06 오프셋 지도
- `port_exe.py` — 실행파일 이식(핵심). 포인터 분류: 절대(KSEG0/KUSEG)/self-relative/
  split lui-ori·lui-addiu 주소/verbatim
- `validate_content.py` — 이식본 UI 테이블 디코딩 대조 검증
- `patch_iso.py` — 이미지 in-place/재배치 패치 + EDC/ECC + .cue

핵심 교훈: **`lui/ori`·`lui/addiu`로 두 명령어에 쪼개진 주소(InitHeap/BSS/scratch
베이스)는 4바이트 워드 스캔으로 안 잡히므로 별도 재조준 필수** — 안 하면
"메모리 확인"에서 프리즈.
