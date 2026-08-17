# scripts/setup.ps1  -  Synaptix Studio Console, first-time setup.
# Creates the virtualenv, installs dependencies, migrates the database,
# bootstraps the CRM, and (if no staff account exists) creates the Lead.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host ""
Write-Host "  Synaptix Studio Console  -  setup" -ForegroundColor Cyan
Write-Host "  Distinction in Design. Discipline in Code." -ForegroundColor DarkGray
Write-Host ""

# 1. Python
try {
    & python --version | Out-Null
} catch {
    Write-Error "Python 3.12+ is required and must be on PATH."
    exit 1
}

# 2. Virtual environment
$pyv = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $pyv)) {
    Write-Host "[1/5] Creating virtual environment..." -ForegroundColor Yellow
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Error "venv creation failed."; exit 1 }
}

# 3. Dependencies
Write-Host "[2/5] Installing dependencies..." -ForegroundColor Yellow
& $pyv -m pip install --upgrade pip --quiet
& $pyv -m pip install -r requirements\local.txt --quiet
if ($LASTEXITCODE -ne 0) { Write-Error "pip install failed."; exit 1 }

# 4. Database + CRM
Write-Host "[3/5] Migrating database..." -ForegroundColor Yellow
& $pyv manage.py migrate --no-input
if ($LASTEXITCODE -ne 0) { Write-Error "migrate failed."; exit 1 }

Write-Host "[4/5] Bootstrapping CRM..." -ForegroundColor Yellow
& $pyv manage.py setup_crm

# 5. Staff account (the Lead)
$staffCount = (& $pyv manage.py shell -c "from django.contrib.auth.models import User; print(User.objects.filter(is_staff=True, is_active=True).count())" | Select-Object -Last 1)
if ([string]::IsNullOrWhiteSpace($staffCount) -or [int]$staffCount -eq 0) {
    Write-Host "[5/5] No staff account yet  -  create the Lead account (the person who manages everything)." -ForegroundColor Yellow
    & $pyv manage.py createsuperuser
} else {
    Write-Host "[5/5] Staff account already exists  -  skipping." -ForegroundColor DarkGray
}

Write-Host ""
Write-Host "  Setup complete." -ForegroundColor Green
Write-Host "  Start the Console:   scripts\run.ps1"
Write-Host "  Then open:           http://localhost:8000/dashboard/"
Write-Host ""
