# 슈퍼로봇대전 컴플리트 박스 한글 KROM 시험판

이 저장소의 `v0.0.1-pre`는 **완성된 한글패치가 아니라 글꼴 표시를 확인하기 위한 BIOS 시각 시험판**입니다. 일본판 PS1 BIOS의 1수준 한자 슬롯 앞 2,350개를 갈무리14의 KS X 1001 완성형 한글 2,350자로 바꿉니다.

![갈무리14 한글 2,350자 KROM 시트](font/hangul_galmuri14_ksx1001_16x15.png)

## 현재 동작과 한계

- 글자당 `16×15`, 1bpp, 30바이트인 PS1 KROM 형식입니다.
- BIOS 오프셋 `0x69D68–0x7B0CB`의 70,500바이트를 교체합니다.
- EUC-KR `B0A1–C8FE`의 한글을 Shift-JIS 슬롯 `889F–94FC` 순서에 대응시켰습니다.
- 게임 대사 번역, 문자 인코딩 변경, 실행 파일 패치는 아직 포함하지 않습니다.
- 따라서 기존 일본어 한자는 의미와 무관한 한글로 보이며, 기호·히라가나·가타카나는 그대로입니다.
- 수정 BIOS를 사용하는 다른 게임의 해당 한자 표시에도 영향을 줍니다.

## xdelta 적용

정당하게 보유한 아래 일본판 SCPH-5500 BIOS의 **복사본**과 별도로 구한 xdelta3가 필요합니다. 원본 BIOS를 덮어쓰지 마십시오.

```text
입력 크기:    524,288 bytes
입력 SHA-256: 9c0421858e217805f4abe18698afea8d5aa36ff0727eb8484944e00eb5e7eadb
출력 SHA-256: 96539cbaabfb79e63409a4348905885d191d8fdde53ba8aa8b7aed57f1534c23
```

PowerShell 보조 스크립트는 입력·출력 해시를 자동으로 검사합니다. `xdelta.exe`를 저장소 루트에 둔 경우:

```powershell
.\apply_patch.ps1 -SourceBios .\scph5500.bin
```

xdelta3를 직접 실행하려면:

```powershell
.\xdelta.exe -d -s .\scph5500.bin .\release\hangul-krom-bios-test-v0.0.1-pre.xdelta .\scph5500.hangul-test.bin
```

출력 파일을 에뮬레이터의 일본판 BIOS로 지정한 뒤 저장 상태가 아닌 콜드 부팅으로 확인하십시오. 게임 BIN/CUE에는 이 패치를 적용하지 않습니다.

## 저장소 구성

- `release/`: 배포용 xdelta와 체크섬
- `font/`: Galmuri14 BDF, 생성한 KROM BIN·PNG, 문자 대응표
- `tools/`: raw BIN 조사, PS1 KROM 덤프, 한글 KROM 생성 도구
- `docs/`: 조사 기록과 릴리스 노트

글꼴 산출물을 다시 만들려면:

```powershell
python -m pip install -r requirements.txt
python .\tools\build_hangul_krom.py .\font\Galmuri14.bdf `
  --font-bin .\font\hangul_galmuri14_ksx1001_16x15.bin `
  --sheet .\font\hangul_galmuri14_ksx1001_16x15.png `
  --mapping .\font\hangul_ksx1001_mapping.tsv
```

`--bios`와 `--patched-bios`를 서로 다른 경로로 함께 지정하면 본인이 보유한 BIOS의 별도 시험 복사본도 만들 수 있습니다. 도구는 원본과 같은 출력 경로를 거부하며, 기존 출력을 바꾸려면 `--force`를 명시해야 합니다. 생성한 BIOS는 저장소나 배포물에 넣지 마십시오.

## 저작권과 배포 범위

이 저장소에는 게임 ROM, 원본·수정 BIOS, 에뮬레이터 또는 xdelta 실행 파일이 포함되지 않습니다. 사용자는 정당하게 보유한 게임과 BIOS를 직접 준비해야 합니다.

[Galmuri14](https://github.com/quiple/galmuri)는 quiple이 제작했으며 SIL Open Font License 1.1로 배포됩니다. 자세한 고지와 라이선스는 [NOTICE.md](NOTICE.md) 및 [LICENSES/Galmuri-OFL-1.1.md](LICENSES/Galmuri-OFL-1.1.md)를 확인하십시오. 별도 표시가 없는 나머지 코드·문서에는 프로젝트 전체 라이선스를 아직 부여하지 않았습니다.

이 프로젝트는 비공식 팬 연구이며 원작사, Sony 또는 Galmuri 제작자와 제휴하거나 승인을 받은 프로젝트가 아닙니다.
