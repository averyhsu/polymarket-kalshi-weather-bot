Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

param(
    [switch]$DryRun,
    [int]$TargetUtcHour = 20
)

$repoRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$mainPath = Join-Path $repoRoot "main.py"
$logDir = Join-Path $repoRoot "logs"
$stateDir = Join-Path $repoRoot "state"
$logPath = Join-Path $logDir "live-demo.log"
$statePath = Join-Path $stateDir "last_live_demo_utc.txt"

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

if (-not (Test-Path $pythonPath)) {
    throw "Python virtual environment not found at $pythonPath"
}
if (-not (Test-Path $mainPath)) {
    throw "main.py not found at $mainPath"
}

$nowUtc = [DateTime]::UtcNow
$todayUtc = $nowUtc.ToString("yyyy-MM-dd")
$shouldRun = $nowUtc.Hour -eq $TargetUtcHour

if (-not $shouldRun) {
    Add-Content -Path $logPath -Value "[$($nowUtc.ToString("o"))] Skipping live demo run; current UTC hour is $($nowUtc.Hour), target is $TargetUtcHour."
    exit 0
}

if (Test-Path $statePath) {
    $lastRunUtc = (Get-Content $statePath -ErrorAction SilentlyContinue | Select-Object -First 1).Trim()
    if ($lastRunUtc -eq $todayUtc) {
        Add-Content -Path $logPath -Value "[$($nowUtc.ToString("o"))] Skipping live demo run; already executed for UTC date $todayUtc."
        exit 0
    }
}

$arguments = @($mainPath, "--mode", "live")
if ($DryRun) {
    $arguments += "--dry-run"
}

Add-Content -Path $logPath -Value "[$($nowUtc.ToString("o"))] Starting live demo run (dry_run=$DryRun)."
Push-Location $repoRoot
try {
    & $pythonPath @arguments *>> $logPath
    if ($LASTEXITCODE -ne 0) {
        throw "Live demo run failed with exit code $LASTEXITCODE"
    }
    Set-Content -Path $statePath -Value $todayUtc -Encoding utf8
    Add-Content -Path $logPath -Value "[$([DateTime]::UtcNow.ToString("o"))] Live demo run completed successfully."
}
finally {
    Pop-Location
}
