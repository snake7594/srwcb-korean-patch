# 조사 기록

## 실행 파일 구성

`SYSTEM.CNF`의 부트 파일은 `SLPS_020.70`입니다. `TR.WAR`, `EX/EX.WAR`, `SECOND/SECOND.WAR`, `THIRD/THIRD.WAR`도 오버레이 조각이 아니라 모두 `0x80010000`에 로드되는 완전한 PS-X EXE입니다. 게임은 `LoadExec(A0:51)`로 이 실행 파일들을 교체 실행합니다.

## 실제 내장 글꼴

다섯 실행 파일에 byte-for-byte 동일한 `0x16000`바이트 글꼴이 있습니다.

| 실행 파일 | 파일 오프셋 | RAM 주소 |
| --- | --- | --- |
| `SLPS_020.70` | `0x1EDB8–0x34DB7` | `0x8002E5B8–0x800445B7` |
| `TR.WAR` | `0x1D520–0x3351F` | `0x8002CD20–0x80042D1F` |
| `EX/EX.WAR` | `0x1D544–0x33543` | `0x8002CD44–0x80042D43` |
| `SECOND/SECOND.WAR` | `0x28058–0x3E057` | `0x80037858–0x8004D857` |
| `THIRD/THIRD.WAR` | `0x2872C–0x3E72B` | `0x80037F2C–0x8004DF2B` |

각 범위 바로 앞의 32비트 word는 해당 글꼴의 RAM 포인터입니다. 공통 blob SHA-256은 다음과 같습니다.

```text
6d84a02c49592abc9b0a7d66d91b5aa132543090a2698ca45af001ad3aea3752
```

형식은 `0xB00`개 글리프 × 32바이트입니다. 한 행의 첫 바이트는 x=0–7, 두 번째 바이트는 x=8–15이며 각 바이트의 bit 7부터 bit 0 순서입니다.

## 문자 파서와 렌더러

EX 실행 파일을 기준으로 한 활성 경로입니다.

- 초기화: `0x8008030C`
- 글꼴 포인터 로드: `0x8008032C`
- parser: `0x8006AAA8`, 핵심 `0x8006AB14`
- rasterizer: `0x80068AE0`, 핵심 `0x80068B48–0x80068B50`
- VRAM upload: `0x8006A5D4`, `0x8006A67C → LoadImage 0x800FDD18`

메시지 바이트는 다음처럼 글리프 인덱스로 변환됩니다.

```text
byte < 0xEB:
    glyphIndex = byte

0xEB <= byte < 0xF6:
    glyphIndex = ((byte - 0xEB) << 8) | nextByte

byte >= 0xF6:
    control code
```

따라서 최대 인덱스 `0xAFF`와 글꼴 개수 `0xB00`이 정확히 일치합니다. 표준 Shift-JIS/JIS 순서가 아니라 게임 전용 문자 사전입니다. 첫 한자가 `工 甲 児 流 竜 馬 神 車 人…` 순으로 이어집니다.

시트 구간은 다음과 같습니다.

- `0x000–0x0FF`: ASCII, 숫자, 히라가나, 가타카나, 기호
- `0x100–0x70F`: 주 한자 사전
- `0x710–0x8BF`: 빈 슬롯 432개
- `0x8C0–0xAFF`: 추가 기호와 한자

원본 한자는 대체로 11×12 픽셀(`x=0–10`, `y=2–13`)입니다. 이번 가시성 시험은 사용자의 요청에 맞춰 갈무리14를 14×14로 배치했습니다.

## BIOS 경로에 대한 정정

실행 파일에는 PsyQ 폰트 라이브러리의 `Krom2RawAdd(B0:51)` 호출도 남아 있습니다. 이 함수는 16×15, 30바이트 BIOS KROM 글리프를 읽는 별도 경로입니다. 처음에는 이를 게임의 주 렌더러로 판단했지만, BIOS 글꼴을 교체해도 게임 화면이 변하지 않았습니다. 이후 실제 게임 parser와 16×16 내장 글꼴의 rasterizer/VRAM upload 경로를 확인하여 결론을 정정했습니다.

## 시험판 배치

인덱스 `0x100`은 rasterizer가 8픽셀 반폭으로 처리하므로 보존했습니다. KS X 1001 한글 2,350자는 `0x101–0xA2E`에 연속 배치합니다.

```text
glyphIndex = 0x101 + hangulIndex   # hangulIndex=0..2349
lead       = 0xEB + (glyphIndex >> 8)
trail      = glyphIndex & 0xFF
```

Track 1의 다섯 EXE를 고정 크기로 교체했으며 MODE2 Form 1 변경 섹터의 EDC/ECC를 다시 계산했습니다. 원본과 패치 결과의 자세한 해시는 릴리스 노트를 참조하십시오.
