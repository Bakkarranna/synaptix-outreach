# scripts/run.ps1 - start the Synaptix Studio Console.
# Starts the outreach daemon (detached, pidfile-tracked) and the web server,
# then opens the dashboard. Pidfiles live in data\ so the dashboard's daemon
# status card sees the same process it can stop.
#
# If something already listens on :8000 (e.g. the server was started by hand),
# the web step is skipped - the Console just points at the running server.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\run.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pyv = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $pyv)) {
    Write-Error "No virtualenv found - run scripts\setup.ps1 first."
    exit 1
}
New-Item -ItemType Directory -Force -Path "data" | Out-Null

function Test-ProcessId([int]$procId) {
    return $null -ne (Get-Process -Id $procId -ErrorAction SilentlyContinue)
}
function Test-PortListening([int]$port) {
    return $null -ne (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

# 1. Daemon - the outreach engine
$daemonPidFile = "data\daemon.pid"
$daemonPid = if (Test-Path $daemonPidFile) { [int](Get-Content $daemonPidFile) } else { 0 }
if ($daemonPid -gt 0 -and (Test-ProcessId $daemonPid)) {
    Write-Host "Daemon already running (pid $daemonPid)."
} else {
    Write-Host "Starting the outreach daemon..."
    $p = Start-Process -FilePath (Resolve-Path $pyv) `
        -ArgumentList "manage.py", "rundaemon", "--log-level", "info" `
        -WorkingDirectory $root `
        -RedirectStandardOutput "data\daemon.log" `
        -RedirectStandardError "data\daemon.err.log" `
        -WindowStyle Hidden -PassThru
    Set-Content -Path $daemonPidFile -Value $p.Id
    Write-Host "  daemon -> pid $($p.Id)"
}

# 2. Web server - the console UI
$webPidFile = "data\web.pid"
$webPid = if (Test-Path $webPidFile) { [int](Get-Content $webPidFile) } else { 0 }
if ($webPid -gt 0 -and (Test-ProcessId $webPid)) {
    Write-Host "Web server already running (pid $webPid)."
} elseif (Test-PortListening 8000) {
    Write-Host "A web server is already listening on :8000 - reusing it (started outside the Console)."
} else {
    Write-Host "Starting the web server..."
    $wp = Start-Process -FilePath (Resolve-Path $pyv) `
        -ArgumentList "manage.py", "runserver", "0.0.0.0:8000", "--noreload" `
        -WorkingDirectory $root `
        -RedirectStandardOutput "data\web.log" `
        -RedirectStandardError "data\web.err.log" `
        -WindowStyle Hidden -PassThru
    Set-Content -Path $webPidFile -Value $wp.Id
    Write-Host "  web -> pid $($wp.Id)"
}

Start-Sleep -Seconds 3
Start-Process "http://localhost:8000/dashboard/"

Write-Host ""
Write-Host "  Console:   http://localhost:8000/dashboard/"
Write-Host "  Daemon:    data\daemon.log (the dashboard shows its tail live)"
Write-Host "  Web:       data\web.log"
Write-Host "  Stop:      scripts\stop.ps1"
Write-Host ""
exit 0
