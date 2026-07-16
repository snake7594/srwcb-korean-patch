# 슈퍼로봇대전 컴플리트 박스 — 제2차 슈퍼로봇대전 한국어 패치

`v0.2.0-pre`는 《슈퍼로봇대전 컴플리트 박스》에 수록된
《제2차 슈퍼로봇대전》의 대사와 메뉴·인터미션을 한국어로 바꾸는
조기 시험판입니다.
원문보다 긴 번역을 잘라 넣지 않고, 메시지 저장소와 포인터를 다시
구성하는 가변 길이 패치를 사용합니다.

## 번역 범위

- 시나리오 대사: 4,616건
- 전투 대사: 17,364건
- 격추·사망 대사: 95건
- 전체 적용 위치: 22,075건
- 중복을 합친 한국어 번역: 6,503종
- 프롤로그·조건·선택지 UI 스크립트: 91건
- 음악·데모 제목: 171건
- 무기·정신기·파츠·지형·능력·시나리오 제목 테이블: 1,754건
- 파일럿·기체 이름: 1,248건
- 제2차 전용 메뉴·인터미션 레코드: 73건
- 제2차 실행 경로의 공통 메뉴 레코드: 5건
- 갈무리14 기반 KS X 1001 한글 2,350자와 추가 글리프

《제3차 슈퍼로봇대전》, 《슈퍼로봇대전 EX》,
《슈퍼로봇대전 외전 마장기신》의 대사와 각 게임 전용 메뉴는 번역하지
않습니다. 이번 릴리스는 《제2차 슈퍼로봇대전》 실행 경로에서 사용하는
메뉴·인터미션·공통 UI를 대상으로 합니다.

## 중요한 시험판 안내

이 버전은 번역 자산 검사, 전체 구조 검사, 11개 회귀 테스트와 xdelta
역적용 검증을 통과했습니다. 다만 처음부터 끝까지의 실기 플레이 검증은
완료되지 않았습니다. 오역, 말투 불일치, 화면 전환 문제 또는 특정 전투
조합에서의 오류가 남아 있을 수 있습니다. 원본 이미지는 별도로 보관하고
시험용으로 사용하십시오.

## 패치 적용

정상 소유한 아래 원본 Track 1과 별도로 구한 xdelta3 실행 파일이
필요합니다. 저장소와 릴리스에는 게임 ROM과 `xdelta.exe`가 포함되지
않습니다.

```text
원본 파일: Super Robot Taisen Complete Box (Track 1).bin
원본 크기: 565,543,104 bytes
원본 SHA-256:
3f25650b588774d55c3bbb5b771779beab408eaca020e9a622133ade323a0f94

패치 결과 크기: 568,607,760 bytes
패치 결과 SHA-256:
ad96c2564d5c0667473f878ae4444d19b57a00056711fcfde8ab287ba0139f6b
```

저장소 루트에 `xdelta.exe`가 있을 때:

```powershell
.\apply_patch.ps1 `
  -SourceTrack1 ".\Super Robot Taisen Complete Box (Track 1).bin" `
  -SourceTrack2 ".\Super Robot Taisen Complete Box (Track 2).bin"
```

`SourceTrack2`는 선택 사항입니다. 지정하면 패치된 Track 1과 원본
Track 2를 연결하는 CUE를 함께 만듭니다. Track 2 자체는 변경하지
않습니다.

직접 적용하려면:

```powershell
.\xdelta.exe -d -s `
  ".\Super Robot Taisen Complete Box (Track 1).bin" `
  ".\release\srwcb-second-korean-v0.2.0-pre.xdelta" `
  ".\Super Robot Taisen Complete Box Second Korean v0.2.0-pre (Track 1).bin"
```

## 가변 길이 패치

한국어 문장을 원래 일본어 바이트 수에 맞추기 위해 화자, 띄어쓰기,
문장 끝을 삭제하지 않습니다.

- 한 줄 26칸, 한 페이지 3줄을 기준으로 줄바꿈과 페이지 전환을 생성
- `2_SCE.BIN`의 시나리오 블록과 B1/B3/B4 참조를 재구성
- `BMESS2.BIN`의 실제 런타임 leaf 대상만 재배치
- `2_DEAD.BIN`의 start/end 슬롯을 재구성
- 실행 파일의 메뉴 스크립트·문자열 테이블·이름 테이블을 확장 영역으로
  재배치하고 모든 포인터를 갱신
- 전투 화자 문자열용 scratch와 실행 파일 확장 영역을 BIOS heap에서 분리
- 커진 네 파일을 새 ISO extent로 옮기고 양방향 디렉터리 필드를 갱신
- 변경한 MODE2 Form 1 섹터의 EDC/ECC 재계산

세부 구조는 [확장 빌드 문서](docs/SECOND_EXPANDED_BUILD.md),
[시나리오 재배치](docs/SECOND_SCE_RELOCATION.md),
[전투·격추 메시지 저장소](docs/SECOND_MESSAGE_ARCHIVES.md)에서 확인할 수
있습니다.

## 소스와 권리

저장소에는 자체 제작 도구, 한국어 번역 overlay, 용어집, 문서와
갈무리14 라이선스만 싣습니다. 게임 실행 파일, 추출된 게임 바이너리,
원본 일본어 대사 원장, BIOS, 에뮬레이터와 xdelta 실행 파일은 배포하지
않습니다. 로컬에서 빌드를 재현하려면 정상 소유한 원본에서 필요한
파일과 원장을 직접 추출해야 합니다.

갈무리14는 SIL Open Font License 1.1로 제공됩니다. 자세한 고지는
[NOTICE](NOTICE.md)와 [갈무리 OFL 전문](LICENSES/Galmuri-OFL-1.1.md)을
확인하십시오.

릴리스별 상세 내용은
[`v0.2.0-pre` 릴리스 노트](docs/RELEASE_NOTES_v0.2.0-pre.md)에
기록되어 있습니다.
