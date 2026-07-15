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
$ExpectedOutputHash = '4749e1c85c28999ae0abc0e9128cbe2b18d113c552f0e93aa2328e092cc317f6'
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
    return [Uri]::UnescapeDataString($baseUri.MakeRelativeUri($targetUri).ToString()).Replace('/', $separator)
}

if (-not $XdeltaPath) {
    $XdeltaPath = Join-Path $PSScriptRoot 'xdelta.exe'
}
if (-not $PatchPath) {
    $PatchPath = Join-Path $PSScriptRoot 'release\srwcb-hangul-exe-font-test-v0.0.2-pre.xdelta'
}

$source = (Resolve-Path -LiteralPath $SourceTrack1).Path
$xdelta = (Resolve-Path -LiteralPath $XdeltaPath).Path
$patch = (Resolve-Path -LiteralPath $PatchPath).Path

if (-not $OutputTrack1) {
    $OutputTrack1 = Join-Path ([IO.Path]::GetDirectoryName($source)) 'Super Robot Taisen Complete Box Hangul Font Test (Track 1).bin'
}
$output = [IO.Path]::GetFullPath($OutputTrack1)
if ($source.Equals($output, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The output path must be different from the source Track 1 path.'
}
$outputDirectory = [IO.Path]::GetDirectoryName($output)

$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sourceHash -ne $ExpectedSourceHash) {
    throw "Unsupported source Track 1. SHA-256: $sourceHash"
}

$track2 = $null
$cue = $null
$cueDirectory = $null
if ($SourceTrack2) {
    $track2 = (Resolve-Path -LiteralPath $SourceTrack2).Path
    $track2Hash = (Get-FileHash -LiteralPath $track2 -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($track2Hash -ne $ExpectedTrack2Hash) {
        throw "Unsupported source Track 2. SHA-256: $track2Hash"
    }

    if (-not $OutputCue) {
        $OutputCue = Join-Path $outputDirectory 'Super Robot Taisen Complete Box Hangul Font Test.cue'
    }
    $cue = [IO.Path]::GetFullPath($OutputCue)
    if ($cue.Equals($output, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The output CUE path must be different from the output Track 1 path.'
    }
    if ((Test-Path -LiteralPath $cue) -and -not $Force) {
        throw "The output CUE already exists. Choose another path or pass -Force: $cue"
    }
    $cueDirectory = [IO.Path]::GetDirectoryName($cue)
}

if (Test-Path -LiteralPath $output) {
    if (-not $Force) {
        throw "The output already exists. Choose another path or pass -Force: $output"
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
        throw "xdelta decode failed with exit code $LASTEXITCODE"
    }

    $outputHash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($outputHash -ne $ExpectedOutputHash) {
        throw "Output verification failed. SHA-256: $outputHash"
    }
}
catch {
    if (Test-Path -LiteralPath $output) {
        Remove-Item -LiteralPath $output -Force
    }
    throw
}

Write-Host 'Track 1 patch and SHA-256 verification completed.'
Write-Host "Output: $output"
Write-Host "SHA-256: $ExpectedOutputHash"

if ($track2) {
    $track1Reference = Get-RelativeFilePath -FromDirectory $cueDirectory -ToPath $output
    $track2Reference = Get-RelativeFilePath -FromDirectory $cueDirectory -ToPath $track2
    $cueLines = @(
        "FILE `"$track1Reference`" BINARY"
        '  TRACK 01 MODE2/2352'
        '    INDEX 01 00:00:00'
        "FILE `"$track2Reference`" BINARY"
        '  TRACK 02 AUDIO'
        '    INDEX 00 00:00:00'
        '    INDEX 01 00:02:00'
    )
    Set-Content -LiteralPath $cue -Value $cueLines -Encoding ascii
    Write-Host "CUE: $cue"
}
