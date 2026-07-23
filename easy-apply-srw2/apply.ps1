#requires -version 3
# 제2차 슈퍼로봇대전 단독판(별매 CD, SLPS_02406) 한글패치 v0.9.3 적용 엔진
# 이 스크립트는 "한글패치 적용하기.bat" 이 자동으로 실행합니다.

$ErrorActionPreference = 'Stop'

$root    = $PSScriptRoot
$xdelta  = Join-Path $root 'xdelta.exe'
$patch   = Join-Path $root 'srw2-standalone-korean-v0.9.3.xdelta'

$T1NAME  = 'Super Robot Taisen 2.img'
$OUTNAME = 'Super Robot Taisen 2 (Korean).img'
$CUENAME = 'Super Robot Taisen 2 (Korean).cue'

$EXP_SRC   = 'a3d3a603da98edcf3d454fba3dda57b112c54d5a1a7af51e6e86bc610bd608bd'
$EXP_OUT   = '1a14d915a0219f726d15bb041c381ddc74794828533579a06d69742a2f2d2032'
$EXP_PATCH = '6ea4242a800c96aff3a7b038e5bffb8f1f35ecb767eb42524170486e19d82028'

function Get-Sha256([string]$p) { return (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower() }
function Close-Window([int]$code) {
    Write-Host ''
    Write-Host '  이 창을 닫으려면 아무 키나 누르세요...' -ForegroundColor DarkGray
    exit $code
}
function Fail([string]$msg) {
    Write-Host ''
    Write-Host "  [오류] $msg" -ForegroundColor Red
    Close-Window 1
}

try {
    Write-Host ''
    Write-Host '============================================================'
    Write-Host '   제2차 슈퍼로봇대전 단독판(별매 CD) 한글패치 v0.9.3'
    Write-Host '============================================================'
    Write-Host ''

    if (-not (Test-Path -LiteralPath $xdelta)) { Fail "xdelta.exe 가 없습니다. 패치 파일들을 한 폴더에 함께 두세요." }
    if (-not (Test-Path -LiteralPath $patch))  { Fail "srw2-standalone-korean-v0.9.3.xdelta 가 없습니다." }

    # --- 원본 .bin 찾기: 드래그앤드롭 인자 > 표준 파일명 > 폴더 내 유일한 .bin ---
    $src = $null
    if ($args.Count -ge 1 -and $args[0] -and (Test-Path -LiteralPath $args[0])) {
        $src = (Resolve-Path -LiteralPath $args[0]).Path
    }
    elseif (Test-Path -LiteralPath (Join-Path $root $T1NAME)) {
        $src = (Resolve-Path -LiteralPath (Join-Path $root $T1NAME)).Path
    }
    else {
        $bins = @(Get-ChildItem -LiteralPath $root -Filter *.img -File | Where-Object { $_.Name -ne $OUTNAME })
        if ($bins.Count -eq 1) { $src = $bins[0].FullName }
    }
    if (-not $src) {
        Write-Host "  원본 단독판 이미지를 찾을 수 없습니다." -ForegroundColor Yellow
        Write-Host ''
        Write-Host "   방법 1) 원본 단독판 .bin 파일을 이 폴더에 넣고 다시 실행"
        Write-Host "   방법 2) 원본 .bin 파일을 '한글패치 적용하기.bat' 아이콘 위로 끌어다 놓기"
        Write-Host ''
        Write-Host "   (파일명이 달라도 됩니다. SHA-256 으로 정품 여부를 확인합니다.)"
        Close-Window 1
    }

    $srcdir = Split-Path -Parent $src
    $out = Join-Path $srcdir $OUTNAME
    $cue = Join-Path $srcdir $CUENAME

    Write-Host "  원본: $src"
    Write-Host ''
    Write-Host '  [1/4] 패치 파일 검증...'
    if ((Get-Sha256 $patch) -ne $EXP_PATCH) { Fail "패치 파일이 손상되었습니다. 다시 내려받으세요." }

    Write-Host '  [2/4] 원본 이미지 검증... (수십 초 걸립니다)'
    $sh = Get-Sha256 $src
    if ($sh -ne $EXP_SRC) {
        Fail ("지원하지 않는 원본입니다 (SHA-256 불일치).`n         정품 제2차 단독판(SLPS-02406) 이미지가 맞는지 확인하세요.`n         현재값: $sh")
    }

    $needPatch = $true
    if (Test-Path -LiteralPath $out) {
        if ((Get-Sha256 $out) -eq $EXP_OUT) {
            Write-Host '  이미 패치가 적용되어 있습니다. CUE 파일만 새로 만듭니다.'
            $needPatch = $false
        } else { Remove-Item -LiteralPath $out -Force }
    }

    if ($needPatch) {
        Write-Host '  [3/4] 한글패치 적용 중... (수십 초 소요)'
        & $xdelta -d -f -s $src $patch $out
        if ($LASTEXITCODE -ne 0) {
            if (Test-Path -LiteralPath $out) { Remove-Item -LiteralPath $out -Force }
            Fail "xdelta 적용이 실패했습니다 (종료 코드 $LASTEXITCODE)."
        }
        Write-Host '  [4/4] 결과 검증...'
        $oh = Get-Sha256 $out
        if ($oh -ne $EXP_OUT) {
            Remove-Item -LiteralPath $out -Force
            Fail ("결과 검증 실패 (SHA-256 불일치). 출력 파일을 삭제했습니다.`n         현재값: $oh")
        }
    }

    # 단독판은 단일 MODE2/2352 데이터 트랙 .cue 로 엽니다 (.ccd 로 열면 멈춤).
    Set-Content -LiteralPath $cue -Encoding ascii -Value @(
        ('FILE "{0}" BINARY' -f $OUTNAME),
        '  TRACK 01 MODE2/2352',
        '    INDEX 01 00:00:00'
    )

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host '   완료되었습니다!' -ForegroundColor Green
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host ''
    Write-Host '  에뮬레이터(DuckStation 등)에서 아래 CUE 파일을 여세요:'
    Write-Host ''
    Write-Host "     $cue" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  * 제2차 전체가 한글로 나옵니다.'
    Write-Host '  * 반드시 생성된 .cue 로 여세요 (.ccd/.cue 원본으로 열면 "메모리 확인"에서 멈춥니다).'
    Write-Host '  * 원본 파일은 그대로 보존됩니다.'
    Close-Window 0
}
catch {
    Fail $_.Exception.Message
}
