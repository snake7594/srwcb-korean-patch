<#
  Apply the Korean patch to the STANDALONE release of 第2次スーパーロボット大戦
  (별매 단독판, SLPS_02406) — the CloneCD .img.

  Requires: your own legally-owned "Super Robot Taisen 2.img" and xdelta.exe
  (not distributed here). Produces a Korean .img plus a single-data-track .cue.

  Usage:
    .\apply_srw2_standalone.ps1 -SourceImg ".\Super Robot Taisen 2.img"
#>
param(
  [Parameter(Mandatory=$true)][string]$SourceImg,
  [string]$Xdelta = ".\xdelta.exe",
  [string]$Patch  = ".\release\srw2-standalone-korean-v0.9.2.xdelta",
  [string]$OutName = "Super Robot Taisen 2 (Korean)"
)

$ErrorActionPreference = "Stop"
$RetailSha = "a3d3a603da98edcf3d454fba3dda57b112c54d5a1a7af51e6e86bc610bd608bd"
$KoreanSha = "acf1c391201093980659993ab823a650b1923228726623370ecf2e25f03c887d"

if (-not (Test-Path $SourceImg)) { throw "Source image not found: $SourceImg" }
if (-not (Test-Path $Xdelta))    { throw "xdelta.exe not found: $Xdelta (obtain separately)" }
if (-not (Test-Path $Patch))     { throw "Patch not found: $Patch" }

$srcHash = (Get-FileHash -Algorithm SHA256 $SourceImg).Hash.ToLower()
if ($srcHash -ne $RetailSha) {
  Write-Warning "Source .img SHA-256 does not match the expected retail image."
  Write-Warning "  expected: $RetailSha"
  Write-Warning "  got     : $srcHash"
  Write-Warning "The patch will only apply cleanly to the exact retail dump."
}

$dir     = Split-Path -Parent (Resolve-Path $SourceImg)
$OutImg  = Join-Path $dir "$OutName.img"
$OutCue  = Join-Path $dir "$OutName.cue"

Write-Host "Applying patch -> $OutImg"
& $Xdelta -d -f -s $SourceImg $Patch $OutImg
if ($LASTEXITCODE -ne 0) { throw "xdelta failed ($LASTEXITCODE)" }

$outHash = (Get-FileHash -Algorithm SHA256 $OutImg).Hash.ToLower()
if ($outHash -ne $KoreanSha) { Write-Warning "Output SHA-256 mismatch: $outHash" }
else { Write-Host "Output verified (SHA-256 OK)." -ForegroundColor Green }

# single MODE2/2352 data-track cue (open THIS in DuckStation; a CloneCD .ccd freezes,
# because the relocated files live in the disc's trailing null/track-2 region).
"FILE ""$OutName.img"" BINARY`r`n  TRACK 01 MODE2/2352`r`n    INDEX 01 00:00:00`r`n" |
  Set-Content -NoNewline -Encoding ascii $OutCue

Write-Host "Done. In DuckStation open:  $OutCue" -ForegroundColor Cyan
