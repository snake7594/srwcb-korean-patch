# v0.0.2-pre — 실행 파일 내장 한글 글꼴 시험판

## 포함 내용

- 다섯 PS-X EXE의 공통 16×16 내장 글꼴 교체
- 갈무리14 기반 KS X 1001 한글 2,350자
- 한글 배치표와 16×16 BIN·PNG
- MODE2/2352 Track 1용 xdelta
- 입력·출력 SHA-256 검증 PowerShell 스크립트
- 글꼴 추출·주입 및 EDC/ECC 재계산 Python 도구

## 배치

- 보존: 글리프 `0x000–0x100`
- 한글: 글리프 `0x101–0xA2E`
- 첫 메시지 코드: `EC 01`
- 마지막 메시지 코드: `F5 2E`

## 해시

```text
원본 Track 1 SHA-256:
3f25650b588774d55c3bbb5b771779beab408eaca020e9a622133ade323a0f94

패치 Track 1 SHA-256:
4749e1c85c28999ae0abc0e9128cbe2b18d113c552f0e93aa2328e092cc317f6

xdelta SHA-256:
0894206e6fc99ea65ef7d45f9987a73ee925563ecb0c50da8fafea5dbf373cf1

한글 BIN SHA-256:
1e76bedb560081e05210963f4af0004c0b45cfb8dd0e09ac34828d443b175f35
```

## 제한

- 일본어 메시지 번역과 한글 인코딩 패치는 포함하지 않습니다.
- 기존 한자가 임의의 한글로 바뀌는지 확인하는 시험판입니다.
- 에뮬레이터 검증은 사용자가 직접 수행하기 전 상태입니다.
- Track 2는 변경하지 않습니다.
