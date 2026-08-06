# v0.11.2 — 제3차 유닛 '타입' 칸·출격 화면 깨진 글자 수정

[#5](https://github.com/snake7594/srwcb-korean-patch/issues/5) 에서 제보해 주신 제3차
UI 항목 중 **두 곳**을 고쳤습니다. 나머지 두 곳은 아직 남아 있습니다(아래 참고).

## 고친 것

### 유닛 상태창 '타입' 칸

지형 조합표 16칸이 **통째로 미번역**이었습니다. 일본어 글리프 번호가 그대로
남아 있었는데, 그 번호 자리는 한글로 덮여 있어서 화면에는 엉뚱한 글자가 겹쳐
나왔습니다. 이미 번역돼 있던 칸도 `륙` 으로 들어가 표기가 섞여 있었습니다.

`육` `공` `수육` `우주` `공육` `수공` `수육공` `육지중` `공육지중` `수육` `수`
`공지중` — 16칸 전부 정리했습니다.

### 출격 유닛 선택 화면 헤더

`남은 ○機` 의 `機` 가 같은 이유로 깨져 나왔습니다 → `기`.

한자와 한글 모두 2바이트라 자리를 옮기지 않고 제자리에서 바꿉니다. `ui_master`
쪽은 UI-VM 문법으로 토큰을 떠서 글자만 교체합니다(대사 문법으로 읽으면
`0xF0~0xF5` 옵코드를 글자의 앞바이트로 잘못 읽습니다).

## 아직 남은 것

| 제보 | 상태 |
|---|---|
| 맵 명령 메뉴가 `부대 / 반격 / 목적 / 정신` 으로 짤림 | **보류.** 제 이름(`부대표` `반격명령` `작전목적` `정신검색`)을 넣으려면 레코드가 커져 폰트 도너로 옮겨야 하는데, 이건 `ui_master` 의 UI-VM 레코드라 옵코드까지 이해해 다시 써야 합니다. 대사 문법으로 잘라 옮겼더니 레코드가 깨져 되돌렸습니다. (제2차는 같은 메뉴의 레트일 레코드가 더 길어 처음부터 온전합니다.) |
| 타이틀 화면 종료 메시지 일부 일본어 (`シカログ「…」` 등) | **보류.** 한글화한 풀이 `0x7dc2~0x811e` 범위뿐이고, 이 대사들은 그 밖에 있습니다. 타이틀 VM 의 대사 풀 전체를 다시 훑어야 합니다. |

## 검증

컴플리트 박스와 단독판 3종 모두 — 미번역 0 / 상자 넘침 0 / 깨짐 0.
(v0.11.1 에서 고친 대사 레이아웃·승리/패배조건은 그대로 유지됩니다.)

## 적용 방법

정품 디스크에서 뽑은 **Track 1 `.bin`** 에 `xdelta` 패치를 적용합니다.

| 대상 | 패치 파일 |
|------|-----------|
| 컴플리트 박스 | `srwcb-second-third-ex-korean-v0.11.2.xdelta` |
| 제2차 단독판 | `srw2-standalone-korean-v0.11.2.xdelta` |
| 제3차 단독판 | `srw3-standalone-korean-v0.11.2.xdelta` |
| EX 단독판 | `srwex-standalone-korean-v0.11.2.xdelta` |

간편 적용판 `srwcb-cb-korean-v0.11.2-easy-apply.zip` 은 `apply.ps1` 을 실행하면
됩니다(xdelta 포함).

**에뮬레이터에서는 함께 들어 있는 `.cue` 를 여세요.** `.ccd` 로 열면 멈춥니다.
컴플리트 박스는 원본 Track 2 를 같은 폴더에
`Super Robot Taisen Complete Box (Track 2).bin` 이름으로 두세요.

## 체크섬 (SHA-256)

```
dffbfec2e8e3d6f83a1c14580b7ed225cbece8511435a4e3594d48411a555125  srwcb-second-third-ex-korean-v0.11.2.xdelta
8853c710f28a35c8ed068ebd71782407790e19033e8e70d7bab69e903ca729d9  srw2-standalone-korean-v0.11.2.xdelta
09ee01d9333f3bb64fff204a30ec04f0d8bdfe5c56481681f1539d96a7ab8e1c  srw3-standalone-korean-v0.11.2.xdelta
caa19e616a16d356ab224306dc95e0e2c94afe831585e3c8d9607605e86c914b  srwex-standalone-korean-v0.11.2.xdelta
97f20ec3d70b407955f06c74fb87f5ad91cf021d27fecd39389d3b121aa4e2eb  srwcb-cb-korean-v0.11.2-easy-apply.zip

f0f6b338f07e0ace87ef0bbe66fda0af8e803cb0f619f1dd698ea68a47acee5d  Super Robot Taisen Complete Box Korean v0.11.2 (Track 1).bin
```

모든 패치는 **역적용으로 결과 이미지가 그대로 나오는지** 확인했습니다.

## 이전 판

[v0.11.1](RELEASE_NOTES_v0.11.1.md) — 대사 밀림·꼬임, 승리/패배조건, 전투 대사
넘침 수정 (대사 상자 크기를 원문에서 읽어 쓰도록 변경).
