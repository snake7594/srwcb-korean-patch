# v0.11.5 — 제3차 대사 밀림, 남아 있던 몫까지

v0.11.3 에서 쪽 나눔을 걷어냈는데도 밀림이 남는다는 제보를 받고 다시 봤습니다.
두 가지가 겹쳐 있었습니다.

## 1. 검증기가 대사의 절반만 보고 있었습니다

시나리오 대사를 세는 기준이 "블록 머리의 포인터가 가리키는 레코드"뿐이었습니다.
그런데 **풀 안의 이벤트 스크립트가 가리키는 대사**가 그만큼 더 있습니다.

| | 예전 기준 | 실제 |
|---|---:|---:|
| 제2차 | 1,012 | 4,240 |
| 제3차 | 2,540 | **4,630** |
| EX | 3,858 | 4,749 |

그래서 "상자 넘침 0" 이라고 보고했지만 실제로는 남아 있었습니다. 이제 참조 여부를
가리지 않고 **사람이 읽는 한국어 대사면 전부** 검사합니다.

## 2. 넓은 창을 억지로 좁히고 있었습니다

대사 창은 장면마다 크기가 다릅니다. 원문이 **폭 60** 을 쓰는 넓은 창도 있는데,
v0.11.1 부터 일괄 32 로 좁혀 넣었습니다. 그러면 같은 문장이 두 배의 줄을 차지해
상자를 넘칩니다.

이제 **원문이 실제로 쓴 폭 아래로는 좁히지 않습니다**(`max(원문 폭, 32)`).
줄이 줄어드니 쪽 나눔도 덜 필요해졌습니다 — 제3차 F7 259 → 229.

## 검증

기준을 원문 레코드 대비로 맞췄습니다(폭 `max(원문, 32)` / 줄 `max(원문, 3)`).

| 항목 | 레코드 | 미번역 | 상자 넘침 | 깨짐 |
|------|-------:|------:|---------:|-----:|
| 제2차 대사 / 전투 / 사망 | 4,240 / 17,364 / 95 | 0 | 0 | 0 |
| 제3차 대사 / 전투 / 사망 | 4,630 / 16,238 / 157 | 0 | 0 | 0 |
| EX 대사 / 전투 / 사망 | 4,749 / 18,829 / 120 | 0 | 0 | 0 |

컴플리트 박스와 단독판 3종 모두 동일합니다.

## 적용 방법

정품 Track 1 `.bin` 에 `xdelta` 를 적용합니다(컴플리트 박스는
`srwcb-second-third-ex-korean-v0.11.5.xdelta`, 단독판은 각 `*-standalone-*`).
간편 적용판 zip 은 `apply.ps1` 실행. **에뮬레이터에서는 함께 든 `.cue` 를 여세요**
(`.ccd` 는 멈춤). 컴플리트 박스는 원본 Track 2 를 같은 폴더에
`Super Robot Taisen Complete Box (Track 2).bin` 이름으로 두세요.
세이브 데이터는 그대로 쓸 수 있습니다.

## 체크섬 (SHA-256)

```
13aa02c87af19a9c49bd3bc25d8ac6f6665f9cffcccc1909ddffc4e1f99c70d0  srwcb-second-third-ex-korean-v0.11.5.xdelta
8dd607a05778195cde9fa2b379c0b802062d8ec1c92f0bef30da2a0ee1da1dee  srwcb-cb-korean-v0.11.5.cue
00200d499f40562d5c394c9916897d173be12404c12dd28485a59361cd36ffa7  srw2-standalone-korean-v0.11.5.xdelta
68676ab6aa79faad66a86066ae4b90f70a02334d07f24109bacf0b1a1b6da3e7  srw2-standalone-korean-v0.11.5.cue
6b1d7ef012ed284ab72962c56a4c85eb05f91d2e0353496e9a6348f155e3c422  srw3-standalone-korean-v0.11.5.xdelta
74994e19e6ecbfd9414aa1154091ceb2908dab84eb21afc80ac2730ca7ce9575  srw3-standalone-korean-v0.11.5.cue
ea11b9c6f4cea1590242e271a9a72fa9c86802bef729c9965dd265543796883a  srwex-standalone-korean-v0.11.5.xdelta
17b44fa6bb26fa4f410b3e517d9a483994cfc82f7ea9627150f06debe7d9375b  srwex-standalone-korean-v0.11.5.cue
595a657dc9c26a6a4cc2cdeaf904145dfaa045316aa496bc14c949b41ae91511  srwcb-cb-korean-v0.11.5-easy-apply.zip

# 패치 결과 이미지
69000f58efa10cb43d45a9804064d88955b34bba3225fd3875e239b4ece02252  Super Robot Taisen Complete Box Korean v0.11.5 (Track 1).bin```

모든 패치는 역적용으로 결과 이미지가 그대로 나오는지 확인했습니다.

## 아직 남은 것 (제보 #5)

* 제3차 맵 명령 메뉴 `부대/반격/목적/정신` 짤림 — UI-VM 레코드 재작성 필요
* 제3차 `실드` 옆 `끔/켬`, 세이브 목록 `자료01` 한 칸 밀림
* 제3차 타이틀 종료 메시지 일부 일본어

## 이전 판

[v0.11.4](RELEASE_NOTES_v0.11.4.md) 증원 뒤 멈춤 ·
[v0.11.3](RELEASE_NOTES_v0.11.3.md) 쪽 나눔 제거 ·
[v0.11.2](RELEASE_NOTES_v0.11.2.md) 제3차 '타입' 칸
