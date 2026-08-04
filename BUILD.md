# 직접 빌드하기

이 저장소만 있으면 **자기가 가진 디스크로 한글 패치를 처음부터 만들 수 있습니다.**
번역·도구·폰트는 전부 들어 있고, 게임에서 나온 것(실행파일·대사 원문)만 각자
디스크에서 뽑습니다.

## 준비물

- **정품 《슈퍼로봇대전 컴플리트 박스》** 에서 뽑은 `Track 1` `.bin`
  (MODE2/2352, 565,543,104 바이트)
- **Python 3.11 이상**
- 디스크 여유 공간 약 4 GB
- (선택) `xdelta3` — 배포용 `.xdelta` 패치 파일까지 만들 때만 필요

```bash
pip install -r requirements.txt
```

## 두 줄이면 끝

```bash
python setup_workspace.py --disc "…/Super Robot Taisen Complete Box (Track 1).bin"
python build_all.py
```

결과물은 `work/` 아래에 생깁니다. 에뮬레이터에서는 만들어진 **`.cue`** 를 여세요
(`.ccd` 로 열면 멈춥니다).

## 각 단계가 하는 일

`setup_workspace.py` — 게임에서 나오는 것들을 준비합니다.

| 단계 | 내용 |
|------|------|
| 1 | 디스크에서 실행파일·대사 아카이브 15개 추출 |
| 2 | 저장소의 번역 자산을 작업 폴더에 연결 |
| 3 | 게임 폰트에 한글 2,350자를 심어 한글 폰트 생성 |
| 4 | 폰트만 주입한 기준 트랙 생성 |
| 5 | 실행파일 UI 인벤토리 생성(포인터·레코드 구조 분석) |

`build_all.py` — 번역을 실제로 넣습니다.

| 단계 | 내용 |
|------|------|
| 1 | 원문 대사 후보 추출 |
| 2 | 번역 원장 생성 (제2차 22,075 / 제3차 21,244 / EX 24,189건) |
| 3 | 제2차 빌드 |
| 4 | 제3차 빌드 |
| 5 | EX 빌드 |
| 6 | 트레이닝 모드 빌드 |
| 7 | 디스크 이미지 조립 |
| 8 | 검증 |

오래 걸리는 앞 단계를 건너뛰려면 `--from 3`, 한 단계만 돌리려면 `--only 3`.

## 폴더가 어디에 생기나

기본은 저장소 안 `work/` 입니다. 다른 곳에 두려면 환경변수를 쓰세요.

```bash
set SRWCB_WORK=D:\srwcb-build
set SRWCB_DISC=D:\games\Super Robot Taisen Complete Box (Track 1).bin
```

단독판까지 만들 때는 각자 이미지 경로도 지정합니다.

```bash
set SRWCB_SRW2_IMG=D:\games\Super Robot Taisen 2.img
set SRWCB_SRW3_BIN=D:\games\Dai 3 Ji Super Robot Taisen.bin
set SRWCB_SRWEX_IMG=D:\games\Super Robot Taisen EX (J).img
```

```
work/
  extracted/   디스크에서 뽑은 원본 (배포 금지)
  ledger/      원문 대사 원장 (배포 금지, 여기서 생성)
  build/       중간 산출물
  out/         완성 이미지·패치
```

## 왜 원장은 저장소에 없나

`ledger/` 의 원장은 **게임의 일본어 원문 전체**(합계 약 157 MB)입니다. 게임에서
나온 저작물이라 배포하지 않고, 위 1~2단계에서 각자 디스크로 만듭니다. 만들어진
원장은 원본과 **바이트 단위로 같습니다**(레코드 수·오프셋 전부 일치 확인).

한국어 번역, 도구, 폰트, 용어집은 전부 저장소에 있습니다.

## 폴더 안내

| 폴더 | 내용 |
|------|------|
| `tools/` | 추출·코덱·폰트·재배치 등 공용 도구 |
| `translation/` | 한국어 번역 오버레이·용어집 |
| `font/` | 갈무리14 BDF, 글리프 매핑 |
| `third-ui/` `ex-ui/` `tr-ui/` | 게임별 UI 주입기 |
| `image-build/` | 디스크 이미지 조립 |
| `standalone/` `standalone3/` `standalone_ex/` | 단독판 이식 |
| `second-fixes/` `menu-align/` `audit/` | 버그 수정·정렬·검증 도구 |
| `easy-apply*/` | 배포용 간편 적용 스크립트 |

## 검증

빌드가 끝나면 검사 도구로 확인할 수 있습니다.

```bash
python tr-ui/verify_tr_glyphs.py          # 글리프 무결성
python menu-align/menu_align_audit.py     # 메뉴 칸 정렬
```

`audit/audit_all.py` 는 라이브러리라 임포트해서 씁니다. 잔여 일본어·대사창 폭·
포인터 무결성을 검사하는 방법과, 이 검사에서 흔히 나오는 **오탐 3종**은
[`audit/README.md`](audit/README.md) 에 정리돼 있습니다.

## 문제가 생기면

- **`[없음] 디스크 이미지`** — `--disc` 경로나 `SRWCB_DISC` 를 확인하세요.
- **`디스크가 컴플리트 박스가 맞는지 확인하세요`** — 단독판이나 다른 판입니다.
  이 빌드는 컴플리트 박스(4개 타이틀 합본) 기준입니다.
- **`xdelta executable not found`** — 무시해도 됩니다. 이미지 자체는 만들어집니다.
  패치 파일까지 원하면 `xdelta3` 를 `work/xdelta.exe` 로 두세요.

## 배포하지 않는 것

게임 실행파일, 추출한 게임 데이터, 원문 대사 원장, BIOS, 에뮬레이터,
`xdelta.exe` 는 저장소에 없습니다. 정품에서 직접 준비하세요.
