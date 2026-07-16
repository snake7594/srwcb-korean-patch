# 한국어 번역 자료

`second_translation_overlay.json`에는 6,503개의 한국어 번역과 적용
메타데이터만 들어 있습니다. 원본 일본어 문장, 원본 바이트와 게임 파일
오프셋을 보존한 source ledger는 저작권 자료에서 재구성한 것이므로
배포하지 않습니다.

- overlay schema: `srwcb-second-translation-overlay-v2`
- 번역 키: 6,503/6,503
- 적용 위치: 로컬 source ledger 기준 22,075건
- 승인 용어집 SHA-256:
  `d97a135c9ec8566d330d14b5b72aebad3d23e2e0307888b63c5c51bf109fb9b9`
- overlay SHA-256:
  `709cdf436ccc6a2003f725dcfe6456e22baea3065660c9e0ef69c2e1af643ca3`

이 프리릴리스의 번역 항목은 `translated_unreviewed` 상태입니다. 전체
독립 인적 검수 완료를 뜻하지 않습니다.

`glossary_candidates.json`의 `ko_approved`가 현재 고정 표기이며,
판단 기록은 `APPROVED_GLOSSARY_NOTES.md`에 있습니다.

## 메뉴·인터미션 자산

`v0.2.0-pre`부터 제2차 실행 파일의 UI 번역도 별도 overlay로 제공합니다.
공개 파일에는 한국어 번역, 구조 식별자, 포인터 위치와 원본 레코드
SHA-256 guard만 남기고 일본어 문자열과 원본 바이트는 제거했습니다.

- `second_ui_inventory.json`: UI 구조 레코드 3,608건
- `second_ui_scripts_overlay.json`: 스크립트 335레코드, 708개 치환
- `second_ui_tables_overlay.json`: 메뉴 테이블 1,754건
- `second_ui_names_overlay.json`: 파일럿·기체 이름 1,248건
- `second_ui_common_master_overlay.json`: 공통 메뉴 레코드 5건

빌더는 guard가 일치하지 않거나 번역이 비어 있거나 인코딩할 수 없는
글자가 있으면 패치 생성을 중단합니다.
