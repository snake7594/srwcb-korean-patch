[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceTrack1,

    [string]$SourceTrack2,

    [string]$OutputTrack1,

    [string]$OutputCue,

    [string]$XdeltaPath,

    [string]$PatchPath,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$ExpectedSourceHash = '3f25650b588774d55c3bbb5b771779beab408eaca020e9a622133ade323a0f94'
$ExpectedOutputHash = 'e5d28d78005b08b5f1abbd7a381440c3f90438fc67657dafe62e26cb6b2206be'
$ExpectedPatchHash = 'ff5e532f1e9e1626df74e48adf86559d6b919a8db2cfb5b30799a93a9ba3e78c'
$ExpectedTrack2Hash = '2fbf5a94ffc8b475741529c4a95d580c937ca37db31db227e0d6c7a917a1e95f'

function Get-RelativeFilePath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FromDirectory,

        [Parameter(Mandatory = $true)]
        [string]$ToPath
    )

    $separator = [IO.Path]::DirectorySeparatorChar
    $base = [IO.Path]::GetFullPath($FromDirectory).TrimEnd($separator) + $separator
    $baseUri = [Uri]$base
    $targetUri = [Uri]([IO.Path]::GetFullPath($ToPath))
    if ($baseUri.Scheme -ne $targetUri.Scheme) {
        return $targetUri.LocalPath
    }
    return [Uri]::UnescapeDataString(
        $baseUri.MakeRelativeUri($targetUri).ToString()
    ).Replace('/', $separator)
}

if (-not $XdeltaPath) {
    $XdeltaPath = Join-Path $PSScriptRoot 'xdelta.exe'
}
if (-not $PatchPath) {
    $PatchPath = Join-Path $PSScriptRoot 'release\srwcb-second-korean-v0.2.1-pre.xdelta'
}

$source = (Resolve-Path -LiteralPath $SourceTrack1).Path
$xdelta = (Resolve-Path -LiteralPath $XdeltaPath).Path
$patch = (Resolve-Path -LiteralPath $PatchPath).Path

$patchHash = (Get-FileHash -LiteralPath $patch -Algorithm SHA256).Hash.ToLowerInvariant()
if ($patchHash -ne $ExpectedPatchHash) {
    throw "지원하지 않거나 손상된 xdelta입니다. SHA-256: $patchHash"
}

if (-not $OutputTrack1) {
    $OutputTrack1 = Join-Path (
        [IO.Path]::GetDirectoryName($source)
    ) 'Super Robot Taisen Complete Box Second Korean v0.2.1-pre (Track 1).bin'
}
$output = [IO.Path]::GetFullPath($OutputTrack1)
if ($source.Equals($output, [StringComparison]::OrdinalIgnoreCase)) {
    throw '출력 경로는 원본 Track 1 경로와 달라야 합니다.'
}
$outputDirectory = [IO.Path]::GetDirectoryName($output)

$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sourceHash -ne $ExpectedSourceHash) {
    throw "지원하지 않는 원본 Track 1입니다. SHA-256: $sourceHash"
}

$track2 = $null
$cue = $null
$cueDirectory = $null
if ($SourceTrack2) {
    $track2 = (Resolve-Path -LiteralPath $SourceTrack2).Path
    $track2Hash = (
        Get-FileHash -LiteralPath $track2 -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($track2Hash -ne $ExpectedTrack2Hash) {
        throw "지원하지 않는 원본 Track 2입니다. SHA-256: $track2Hash"
    }

    if (-not $OutputCue) {
        $OutputCue = Join-Path (
            $outputDirectory
        ) 'Super Robot Taisen Complete Box Second Korean v0.2.1-pre.cue'
    }
    $cue = [IO.Path]::GetFullPath($OutputCue)
    if ($cue.Equals($output, [StringComparison]::OrdinalIgnoreCase)) {
        throw '출력 CUE 경로는 출력 Track 1 경로와 달라야 합니다.'
    }
    if ((Test-Path -LiteralPath $cue) -and -not $Force) {
        throw "출력 CUE가 이미 있습니다. 다른 경로를 선택하거나 -Force를 사용하십시오: $cue"
    }
    $cueDirectory = [IO.Path]::GetDirectoryName($cue)
}

if (Test-Path -LiteralPath $output) {
    if (-not $Force) {
        throw "출력 파일이 이미 있습니다. 다른 경로를 선택하거나 -Force를 사용하십시오: $output"
    }
    Remove-Item -LiteralPath $output -Force
}

if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
}
if ($cueDirectory -and -not (Test-Path -LiteralPath $cueDirectory)) {
    New-Item -ItemType Directory -Path $cueDirectory | Out-Null
}

try {
    & $xdelta -d -s $source $patch $output
    if ($LASTEXITCODE -ne 0) {
        throw "xdelta 적용이 종료 코드 ${LASTEXITCODE}(으)로 실패했습니다."
    }

    $outputHash = (
        Get-FileHash -LiteralPath $output -Algorithm SHA256
    ).Hash.ToLowerInvariant()
    if ($outputHash -ne $ExpectedOutputHash) {
        throw "출력 검증에 실패했습니다. SHA-256: $outputHash"
    }
}
catch {
    if (Test-Path -LiteralPath $output) {
        Remove-Item -LiteralPath $output -Force
    }
    throw
}

Write-Host 'Track 1 패치와 SHA-256 검증을 완료했습니다.'
Write-Host "출력: $output"
Write-Host "SHA-256: $ExpectedOutputHash"

if ($track2) {
    $track1Reference = Get-RelativeFilePath -FromDirectory $cueDirectory -ToPath $output
    $track2Reference = Get-RelativeFilePath -FromDirectory $cueDirectory -ToPath $track2
    $quote = [char]34
    $cueLines = @(
        "$quote$track1Reference$quote BINARY" -replace '^', 'FILE '
        '  TRACK 01 MODE2/2352'
        '    INDEX 01 00:00:00'
        "$quote$track2Reference$quote BINARY" -replace '^', 'FILE '
        '  TRACK 02 AUDIO'
        '    INDEX 00 00:00:00'
        '    INDEX 01 00:02:00'
    )
    Set-Content -LiteralPath $cue -Value $cueLines -Encoding ascii
    Write-Host "CUE: $cue"
}
