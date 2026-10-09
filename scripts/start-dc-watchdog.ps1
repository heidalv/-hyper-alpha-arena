# Idempotent launcher for the data-center watchdog: guarantees EXACTLY ONE instance.
#
# Why this exists (2026-10-04 incident, workflow 4):
#   Three independent launch paths (start-dev.ps1, stop-dev.ps1 keep-alive block I added,
#   and the watchdog's own vbs self-restart) could each spawn a watchdog.
#   Measured: `watchdog_count: 2` in scripts/verify-data-center.ps1, and the watchdog log
#   shows instances fighting over the same DC:
#     04:36:04 another instance already running (pid=16124) - exit
#   Duplicate supervisors are dangerous here because the "zombie" path used to kill the DC,
#   so two supervisors = two killers.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-dc-watchdog.ps1
#   ... -Quiet     (no console output)
#   ... -Stop      (stop all watchdogs; useful before starting a fresh patched one)
#
# Exit codes: 0 = exactly one watchdog is running (started or reused).
param(
    [switch]$Quiet,
    [switch]$Stop
)

$ErrorActionPreference = 'SilentlyContinue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$wdScript = Join-Path $PSScriptRoot 'data-center-watchdog.ps1'

function Get-DcWatchdogs {
    @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
        Where-Object { $_.CommandLine -match 'data-center-watchdog' -and $_.ProcessId -ne $PID })
}

$existing = Get-DcWatchdogs

if ($Stop) {
    foreach ($p in $existing) { Stop-Process -Id $p.ProcessId -Force }
    if (-not $Quiet) { Write-Host ("==> dc-watchdog stopped (" + @($existing).Count + " process(es))") }
    exit 0
}

if (@($existing).Count -ge 1) {
    $sorted = @($existing | Sort-Object ProcessId)
    $extras = @($sorted | Select-Object -Skip 1)
    foreach ($p in $extras) { Stop-Process -Id $p.ProcessId -Force }
    $msg = "==> dc-watchdog already running (pid=" + $sorted[0].ProcessId + ")"
    if ($extras.Count -gt 0) { $msg = $msg + ", killed " + $extras.Count + " duplicate(s)" }
    if (-not $Quiet) { Write-Host $msg }
    exit 0
}

if (-not (Test-Path $wdScript)) {
    if (-not $Quiet) { Write-Host ("==> [FAIL] watchdog script not found: " + $wdScript) }
    exit 1
}

Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $wdScript) `
    -WindowStyle Minimized

# WMI 有延迟：固定 2 秒会误报 [FAIL] found 0（实测）。改为最多 6 次 × 2 秒轮询，
# 并以看门狗日志出现 'started (port=' 作为就绪判据之一。
$logPath = Join-Path $RepoRoot 'logs\data-center-watchdog.log'
$now = @()
for ($i = 0; $i -lt 6; $i++) {
    Start-Sleep -Seconds 2
    $now = Get-DcWatchdogs
    if (@($now).Count -ge 1) { break }
}
if (@($now).Count -eq 1) {
    if (-not $Quiet) { Write-Host ("==> dc-watchdog started (pid=" + @($now)[0].ProcessId + ", exactly one)") }
    exit 0
}
if (-not $Quiet) { Write-Host ("==> [FAIL] expected 1 watchdog, found " + @($now).Count + " (waited 12s)") }
exit 1
