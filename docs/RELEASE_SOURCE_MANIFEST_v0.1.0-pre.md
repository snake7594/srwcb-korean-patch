# v0.1.0-pre 저장소 선별 기준

## 포함

- `release/srwcb-second-korean-v0.1.0-pre.xdelta`
- `release/SHA256SUMS.txt`
- `apply_patch.ps1`
- 가변 길이 빌드와 구조 검증에 필요한 `tools/*.py`
- 한글 인코딩에 필요한 검수 완료 글리프 매핑
- `translation/second_translation_overlay.json`
- 승인 용어집과 검수 기록
- 메시지 저장소·재배치 기술 문서
- 갈무리14 BDF와 SIL Open Font License 1.1

## 제외

- 완성·중간 Track 1/Track 2 BIN과 CUE
- 추출한 `SLPS_020.70`, `*.WAR`, `2_SCE.BIN`, `BMESS2.BIN`,
  `2_DEAD.BIN` 및 기타 게임 데이터
- 일본어 원문 문장·원본 바이트·오프셋을 보존한 source ledger
- 번역 작업 배치와 폐기 번역 캐시
- BIOS, 에뮬레이터, 세이브, 스크린샷과 `xdelta.exe`

source ledger와 추출 바이너리는 로컬 재현 입력으로만 사용합니다.
GitHub 저장소와 릴리스 자산에는 넣지 않습니다.
