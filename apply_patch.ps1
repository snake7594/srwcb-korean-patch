[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$SourceBios,

    [string]$OutputBios,

    [string]$XdeltaPath,

    [string]$PatchPath,

    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$ExpectedSourceHash = '9c0421858e217805f4abe18698afea8d5aa36ff0727eb8484944e00eb5e7eadb'
$ExpectedOutputHash = '96539cbaabfb79e63409a4348905885d191d8fdde53ba8aa8b7aed57f1534c23'

if (-not $XdeltaPath) {
    $XdeltaPath = Join-Path $PSScriptRoot 'xdelta.exe'
}
if (-not $PatchPath) {
    $PatchPath = Join-Path $PSScriptRoot 'release\hangul-krom-bios-test-v0.0.1-pre.xdelta'
}

$source = (Resolve-Path -LiteralPath $SourceBios).Path
$xdelta = (Resolve-Path -LiteralPath $XdeltaPath).Path
$patch = (Resolve-Path -LiteralPath $PatchPath).Path

if (-not $OutputBios) {
    $directory = [IO.Path]::GetDirectoryName($source)
    $stem = [IO.Path]::GetFileNameWithoutExtension($source)
    $extension = [IO.Path]::GetExtension($source)
    $OutputBios = Join-Path $directory ($stem + '.hangul-test' + $extension)
}

$output = [IO.Path]::GetFullPath($OutputBios)
if ($source.Equals($output, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The output path must be different from the source BIOS path.'
}

$sourceHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
if ($sourceHash -ne $ExpectedSourceHash) {
    throw "Unsupported source BIOS. SHA-256: $sourceHash"
}

if (Test-Path -LiteralPath $output) {
    if (-not $Force) {
        throw "The output already exists. Choose another path or pass -Force: $output"
    }
    Remove-Item -LiteralPath $output -Force
}

$outputDirectory = [IO.Path]::GetDirectoryName($output)
if (-not (Test-Path -LiteralPath $outputDirectory)) {
    New-Item -ItemType Directory -Path $outputDirectory | Out-Null
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

Write-Host 'Patch application and SHA-256 verification completed.'
Write-Host "Output: $output"
Write-Host "SHA-256: $ExpectedOutputHash"
