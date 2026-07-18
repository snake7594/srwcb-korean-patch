# v0.2.1-pre — 메뉴 런타임 수정 시험판

## 핵심 수정

- 새 게임 시작 직후 메뉴가 깨지거나 전투 진입 전에 멈추던 원인을 수정했습니다.
- 이전 실행 중 arena에 UI 자료를 두는 방식을 폐기하고, `SECOND.WAR` 내부의
  정적 여유 영역(`0x3C938..0x3CD58`)에 메뉴 자료를 재배치했습니다.
- 원본 루트 블록(`0xE14..0x1231`)을 실행 파일 내부 cave로 옮기고 숨은 포인터와
  로드 경계를 갱신했습니다.
- 전투용 scratch가 메뉴 문자열과 겹치지 않도록 보호했습니다.
- 제2차 UI master를 77개 레코드로 정리하고, 메뉴·인터미션 추가 항목 4개를
  포함했습니다. UI overlay는 339개 스크립트 레코드와 727개 교체 span입니다.
- F7의 2바이트 인자 및 compact/extended 상태를 보존하는 stateful 토크나이저를
  적용했습니다. 미리보기 대화의 F7 zero-argument 종료 규칙은 별도로 유지합니다.

## 검증

- 정적 패치, 포인터·숨은 참조 guard, 폰트 donor guard 통과
- 13개 회귀 테스트 통과
- ISO9660 extent, MODE2/Form1 EDC/ECC 검증 통과
- xdelta 역적용 결과가 최종 Track 1과 일치
- 에뮬레이터는 자동 실행하지 않았습니다. 새로 부팅하여 메뉴와 전투 진입을
  직접 확인해 주세요.

## 산출물

- Track 1 크기: 568,386,672 bytes
- Track 1 SHA-256:
  `e5d28d78005b08b5f1abbd7a381440c3f90438fc67657dafe62e26cb6b2206be`
- xdelta 크기: 793,877 bytes
- xdelta SHA-256:
  `ff5e532f1e9e1626df74e48adf86559d6b919a8db2cfb5b30799a93a9ba3e78c`

Track 2는 원본을 그대로 사용합니다. `apply_patch.ps1`에 원본 Track 1,
원본 Track 2와 `xdelta.exe`를 지정하면 테스트용 CUE를 만들 수 있습니다.
