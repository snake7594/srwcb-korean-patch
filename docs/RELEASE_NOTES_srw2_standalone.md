# 릴리스 노트 — 단독판(SLPS_02406) 한글 패치 (v0.8.7 이식)

《제2차 슈퍼로봇대전》을 **단독으로 별매한 CD판**(부트 실행파일 `SLPS_024.06`,
볼륨 `SLPS_02406`)에 컴플리트 박스판 `v0.8.7` 한글 패치를 **이식**한 릴리스입니다.
번역 내용은 컴플리트 박스판과 동일합니다.

## 같은 게임, 다른 실행파일

단독판은 컴플리트 박스판과 **엔진·데이터가 거의 동일**하지만, 부트 실행파일이
다릅니다(`SLPS_024.06` 1,228,800바이트 vs `SECOND.WAR` 1,224,704바이트). 단독판에
2인 플레이용 레코드 등이 삽입되어 있어 그 뒤의 모든 포인터·테이블 오프셋이
구간별로 밀려 있습니다. 따라서 컴플리트 박스판 패치를 **그대로 복사할 수 없고**,
단독판 오프셋 기준으로 **재빌드(이식)** 했습니다.

## 이식 방식

- **delta 지도**: `SECOND.WAR ↔ SLPS_024.06`의 구간별 오프셋 차이(+0x60~+0xd70)를
  앵커 기반으로 정밀 매핑.
- **실행파일 이식**: 검증된 patched `SECOND.WAR`의 변경을 단독판에 옮기되, 각 변경
  워드를 **포인터 의미별로 분류**해 재계산 — 절대(KSEG0/KUSEG) 포인터·self-relative
  포인터(테이블·음악풀)·`lui/ori`·`lui/addiu`로 쪼개진 주소(InitHeap/BSS/battle
  scratch 베이스)·그 외 텍스트/글리프(verbatim). 재배치 텍스트/폰트 영역은 포인터
  오분류를 막기 위해 verbatim 처리.
- **검증**: 이식본의 UI 테이블 포인터를 따라가 디코딩한 한글이 원본과 전부 일치,
  의도 외 변경(부패) 0, 부팅 크리티컬(InitHeap/BSS/heap 경계) 주소 정확.
- **데이터 파일**: 전투 대사(BMESS2)·격추 대사(2_DEAD)·시나리오(2_SCE)는 컴플리트
  박스판의 패치본을 사용. 커진 파일은 디스크 끝 널 패딩 영역에 재배치, MODE2 Form1
  EDC/ECC 재계산.

## 적용 방법

정상 소유한 단독판 이미지(`Super Robot Taisen 2.img`, CloneCD)와 `xdelta.exe`가
필요합니다. 저장소에는 게임 이미지와 `xdelta.exe`를 포함하지 않습니다.

```powershell
.\apply_srw2_standalone.ps1 -SourceImg ".\Super Robot Taisen 2.img"
```

→ `Super Robot Taisen 2 (Korean).img`와 `.cue`가 만들어집니다.

## ⚠️ 반드시 `.cue`로 여세요

DuckStation에서 **생성된 `.cue` 파일을 여세요.** 커진 데이터 파일을 디스크 끝의
빈(원래 트랙2=오디오로 표시된) 영역에 넣었기 때문에, 원본 CloneCD `.ccd`로 열면
데이터 읽기에 실패해 **"메모리 확인"에서 멈춥니다.** `.cue`는 이미지 전체를 하나의
MODE2 데이터 트랙으로 읽게 하여 이 문제를 해결합니다. `.cue`는 `.img`만 참조하므로
`.sub` 파일은 필요 없습니다.

## 파일

- `release/srw2-standalone-korean-v0.8.7.xdelta`
  - SHA-256: `df7e5a536d2d622d2dace3c07ec4ab1188e04ec3cf773f191c7ed493465d9381`
- 원본 이미지 `Super Robot Taisen 2.img`
  - 크기: 578,991,840 bytes / SHA-256:
    `a3d3a603da98edcf3d454fba3dda57b112c54d5a1a7af51e6e86bc610bd608bd`
- 패치 결과 `Super Robot Taisen 2 (Korean).img`
  - 크기: 578,991,840 bytes / SHA-256:
    `d76bcaf231c20319e9b38b8514c7e94e291ceef91bb8544d56985aa32dae06c3`

## 알려진 사항

- 2_SCE(시나리오)는 단독판이 컴플리트 박스판과 한 시나리오 블록에서 8바이트
  다릅니다. 컴플리트 박스판의 패치본을 사용하므로 해당 시나리오는 컴플리트
  박스판 스크립트(같은 게임·번역됨)로 재생됩니다.
- DuckStation 이외의 환경/실기는 검증하지 않았습니다.
