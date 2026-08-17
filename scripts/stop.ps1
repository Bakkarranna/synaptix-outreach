# scripts/stop.ps1 - stop the daemon and the web server.
# Kills the whole process tree (taskkill /T): the venv python.exe is a
# redirector whose real interpreter runs as a child, so killing the recorded
# pid alone can orphan the listener.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

foreach ($name in @("daemon", "web")) {
    $pidFile = "data\$name.pid"
    if (Test-Path $pidFile) {
        $procId = [int](Get-Content $pidFile)
        if ($procId -gt 0 -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
            taskkill /PID $procId /T /F | Out-Null
            Write-Host "Stopped $name (pid $procId)"
        } else {
            Write-Host "$name was not running (stale pidfile removed)"
        }
        Remove-Item $pidFile -Force
    }
}
Write-Host "All stopped. Pipeline state is safe - the daemon resumes where it left off."
