# scripts/package.ps1  -  build a distributable bundle of the Console.
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1           # secrets stay OUT
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -WithConfig  # include config
#
# -WithConfig bundles deploy\onboard_config.json (live credentials) for the
# one-shot VM installer - ONLY for a server you own; never share that bundle.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$includeConfig = $args -contains "-WithConfig"

$stamp = Get-Date -Format "yyyyMMdd-HHmm"
$dist = Join-Path $root "dist"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$out = Join-Path $dist "synaptix-studio-console-$stamp.zip"

if ($includeConfig) {
    Write-Host "  WARNING: bundling deploy\onboard_config.json (live credentials)."
    Write-Host "           Only for your own server. Never share this bundle."
}

$excludes = @(
    ".venv", ".git", "data", "media", "staticfiles",
    ".pytest_cache", ".cache", "dist", "__pycache__", "*.pyc",
    ".env", "deploy/backups"
)
if (-not $includeConfig) { $excludes += "deploy/onboard_config.json" }

$args = @("-a", "-cf", $out)
foreach ($ex in $excludes) { $args += "--exclude=$ex" }
$args += "."

& tar.exe @args
if ($LASTEXITCODE -ne 0) { Write-Error "packaging failed."; exit 1 }

$size = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host ""
Write-Host "  Packaged -> $out ($size MB)" -ForegroundColor Green
if ($includeConfig) {
    Write-Host "  Server: scp this bundle to the VM, then run deploy\install-on-vm.sh"
} else {
    Write-Host "  Recipient: unzip -> scripts\setup.ps1 -> scripts\run.ps1"
}
Write-Host ""
