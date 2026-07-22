# 제3차 단독판(SLPS-02530) 이식 도구

《제3차 슈퍼로봇대전》 대사 한글패치를 컴플리트 박스(THIRD.WAR)에서 별매
단독판(부트 `SLPS_025.30`)으로 delta-transplant 이식합니다.

- `delta_map3.json` — THIRD.WAR ↔ SLPS_025.30 오프셋 존 맵(18 zones, LAST_DELTA=0xd68).
- `port_exe3.py` — 실행파일 패치 이식(BSS-end 절대포인터 재조준, 폰트·임베디드
  BMESS3 테이블 verbatim, 배틀 스크래치 split-address 재유도). 검증: 변경 61,834
  바이트 전부 매핑, stray 0.
- `patch_iso3.py` — 이미지 패치: SLPS 인플레이스 + grown 데이터파일(3_SCE/BMESS3/
  3_DEAD)을 NULL.DA 빈 영역(LBA 232755~)에 재배치 + 단일 MODE2 데이터 트랙 `.cue`.

대사 데이터파일은 컴플리트 박스 빌드 산출물(`test_build/third_korean/rebuilt`)을
재사용합니다(BMESS3/3_DEAD는 단독판과 바이트동일, 3_SCE는 시나리오0만 +384B 상이).
