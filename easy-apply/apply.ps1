#requires -version 3
# 슈퍼로봇대전 컴플리트 박스 한글패치 v0.9.1 (제2차 전체 + 제3차 전체) 적용 엔진
# 이 스크립트는 "한글패치 적용하기.bat" 이 자동으로 실행합니다.
# (직접 실행하려면 원본 Track 1 .bin 을 인자로 넘기거나 같은 폴더에 두세요.)

$ErrorActionPreference = 'Stop'

$root    = $PSScriptRoot
$xdelta  = Join-Path $root 'xdelta.exe'
$patch   = Join-Path $root 'srwcb-second-third-korean-v0.9.1.xdelta'

$T1NAME  = 'Super Robot Taisen Complete Box (Track 1).bin'
$T2NAME  = 'Super Robot Taisen Complete Box (Track 2).bin'
$OUTNAME = 'Super Robot Taisen Complete Box Korean v0.9.1 (Track 1).bin'
$CUENAME = 'Super Robot Taisen Complete Box Korean v0.9.1.cue'

$EXP_SRC   = '3f25650b588774d55c3bbb5b771779beab408eaca020e9a622133ade323a0f94'
$EXP_OUT   = 'd016c4748e4734221cb4c623fd32ffe994ca1bb481ea8d1f7873bfd454c63875'
$EXP_PATCH = '4ad1b66936b11709601b9b9b8fbcd36e7d816797d092e723dbfc07b66d024a0b'

function Get-Sha256([string]$p) {
    return (Get-FileHash -LiteralPath $p -Algorithm SHA256).Hash.ToLower()
}
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
    Write-Host '   슈퍼로봇대전 컴플리트 박스 한글패치 v0.9.1'
    Write-Host '   (제2차 전체 + 제3차 전체)'
    Write-Host '============================================================'
    Write-Host ''

    if (-not (Test-Path -LiteralPath $xdelta)) { Fail "xdelta.exe 가 없습니다. 패치 파일들을 한 폴더에 함께 두세요." }
    if (-not (Test-Path -LiteralPath $patch))  { Fail "srwcb-second-third-korean-v0.9.1.xdelta 가 없습니다." }

    # --- 원본 Track 1 찾기: 드래그앤드롭 인자 > 스크립트 폴더 > 현재 폴더 ---
    $src = $null
    if ($args.Count -ge 1 -and $args[0] -and (Test-Path -LiteralPath $args[0])) {
        $src = (Resolve-Path -LiteralPath $args[0]).Path
    }
    elseif (Test-Path -LiteralPath (Join-Path $root $T1NAME)) {
        $src = (Resolve-Path -LiteralPath (Join-Path $root $T1NAME)).Path
    }
    elseif (Test-Path -LiteralPath (Join-Path (Get-Location).Path $T1NAME)) {
        $src = (Resolve-Path -LiteralPath (Join-Path (Get-Location).Path $T1NAME)).Path
    }
    if (-not $src) {
        Write-Host "  원본 게임 파일을 찾을 수 없습니다:" -ForegroundColor Yellow
        Write-Host "     $T1NAME"
        Write-Host ''
        Write-Host "   방법 1) 위 원본 파일(Track 1, Track 2)을 이 폴더에 복사한 뒤 다시 실행"
        Write-Host "   방법 2) 원본 Track 1 .bin 을 '한글패치 적용하기.bat' 아이콘 위로 끌어다 놓기"
        Close-Window 1
    }

    $srcdir = Split-Path -Parent $src
    $out = Join-Path $srcdir $OUTNAME
    $cue = Join-Path $srcdir $CUENAME
    $t2  = Join-Path $srcdir $T2NAME

    Write-Host "  원본: $src"
    Write-Host ''
    Write-Host '  [1/4] 패치 파일 검증...'
    if ((Get-Sha256 $patch) -ne $EXP_PATCH) { Fail "패치 파일이 손상되었습니다. 다시 내려받으세요." }

    Write-Host '  [2/4] 원본 디스크 검증... (수십 초 걸립니다)'
    $sh = Get-Sha256 $src
    if ($sh -ne $EXP_SRC) {
        Fail ("지원하지 않는 원본입니다 (SHA-256 불일치).`n         정품 컴플리트 박스 Track 1 .bin 이 맞는지 확인하세요.`n         현재값: $sh")
    }

    $needPatch = $true
    if (Test-Path -LiteralPath $out) {
        if ((Get-Sha256 $out) -eq $EXP_OUT) {
            Write-Host '  이미 패치가 적용되어 있습니다. CUE 파일만 새로 만듭니다.'
            $needPatch = $false
        } else {
            Remove-Item -LiteralPath $out -Force
        }
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

    if (-not (Test-Path -LiteralPath $t2)) {
        Write-Host ''
        Write-Host "  [경고] 오디오 트랙을 찾지 못했습니다: $T2NAME" -ForegroundColor Yellow
        Write-Host "         원본 Track 2 .bin 을 같은 폴더에 두어야 음악이 재생됩니다."
    }

    $cueLines = @(
        ('FILE "{0}" BINARY' -f $OUTNAME),
        '  TRACK 01 MODE2/2352',
        '    INDEX 01 00:00:00',
        ('FILE "{0}" BINARY' -f $T2NAME),
        '  TRACK 02 AUDIO',
        '    INDEX 00 00:00:00',
        '    INDEX 01 00:02:00'
    )
    Set-Content -LiteralPath $cue -Value $cueLines -Encoding ascii

    Write-Host ''
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host '   완료되었습니다!' -ForegroundColor Green
    Write-Host '============================================================' -ForegroundColor Green
    Write-Host ''
    Write-Host '  에뮬레이터(DuckStation 등)에서 아래 CUE 파일을 여세요:'
    Write-Host ''
    Write-Host "     $cue" -ForegroundColor Cyan
    Write-Host ''
    Write-Host '  * 제2차는 전체 한글화, 제3차도 대사+메뉴 전체가 한글로 나옵니다.'
    Write-Host '  * 원본 파일은 그대로 보존됩니다.'
    Write-Host '  * 반드시 .cue 파일로 여세요 (.bin 을 직접 열지 마세요).'
    Close-Window 0
}
catch {
    Fail $_.Exception.Message
}
