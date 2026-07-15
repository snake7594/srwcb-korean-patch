# v0.0.1-pre — 한글 KROM BIOS 시각 시험판

슈퍼로봇대전 컴플리트 박스의 글꼴 표시 경로를 확인하기 위한 첫 임시 릴리스입니다.

## 포함 내용

- 일본판 SCPH-5500 BIOS용 xdelta 패치
- Galmuri14 기반 KS X 1001 한글 2,350자 KROM BIN·PNG
- EUC-KR ↔ Shift-JIS KROM 슬롯 대응표
- 입력·출력 SHA-256을 검사하는 PowerShell 적용 스크립트
- 조사 및 재현용 Python 도구

## 중요한 제한

- 완성된 게임 한글패치가 아닙니다.
- 게임 대사 번역이나 실행 파일의 문자 인코딩 변경은 없습니다.
- 기존 일본어 한자 2,350자가 의미와 무관한 한글로 표시됩니다.
- 정확히 지정된 SCPH-5500 BIOS에만 적용할 수 있습니다.
- 원본·수정 BIOS, 게임 ROM, 에뮬레이터 및 xdelta 실행 파일은 포함하지 않습니다.

## 검증값

```text
입력 BIOS SHA-256:  9c0421858e217805f4abe18698afea8d5aa36ff0727eb8484944e00eb5e7eadb
출력 BIOS SHA-256:  96539cbaabfb79e63409a4348905885d191d8fdde53ba8aa8b7aed57f1534c23
xdelta SHA-256:     03748915e62ff9aabb4ee91ffcdbdb00984227d261d9704e4495738f254718de
한글 KROM SHA-256: 3dbf5d55568006a4008d23125b30e62403372f73da0707166d7d1fe56ea70b6b
```

에뮬레이터 화면 확인은 배포 후 별도로 진행합니다.
