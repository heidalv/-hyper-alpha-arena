[CmdletBinding()]
param(
    [int]$HealthPort = 9100,
    [int]$IntervalSec = 30,
    [int]$FailThreshold = 3,
    [int]$HealthTimeoutSec = 10,
    [int]$GraceAfterRestartSec = 90,
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'SilentlyContinue'
$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path $ScriptDir -Parent
$LogFile = Join-Path $RepoRoot 'logs\data-center-watchdog.log'
New-Item -ItemType Directory -Force -Path (Join-Path $RepoRoot 'logs') | Out-Null
function Write-DcLog([string]$msg) {
    $line = '{0} [dc-watchdog] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line -ForegroundColor DarkCyan
}

# [2026-08-07 fix] must use project venv python (system python lacks sqlalchemy)
function Resolve-DcPython {
    if ($PythonExe -and (Test-Path $PythonExe)) {
        return $PythonExe
    }
    $candidates = @(
        (Join-Path $RepoRoot '.venv\Scripts\python.exe'),
        (Join-Path $RepoRoot '.runtime\Python312\python.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return 'python'
}
$DcPython = Resolve-DcPython
Write-DcLog ('using python: ' + $DcPython)
function Test-DcHealth {
    try {
        $uri = 'http://127.0.0.1:' + $HealthPort + '/health'
        $r = Invoke-WebRequest -Uri $uri -TimeoutSec $HealthTimeoutSec -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-DcPids {
    $procs = Get-CimInstance Win32_Process -Filter 'Name=''python.exe'''
    return @($procs | Where-Object { $_.CommandLine -match 'backend\.workers\.market_data_center' } | ForEach-Object { $_.ProcessId })
}

function Restart-Dc {
    Start-Sleep -Seconds 3
    if (Test-DcHealth) {
        Write-DcLog 'pre-restart probe OK - skip restart'
        return
    }
    Write-DcLog ('DC down - restarting (port ' + $HealthPort + ')')

    $pids = Get-DcPids
    foreach ($procId in $pids) {
        taskkill /PID $procId /T 2>$null | Out-Null
    }
    Start-Sleep -Seconds 5
    $stale = Get-DcPids
    if ($stale) {
        foreach ($procId in $stale) {
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 2
    }

    $portDeadline = (Get-Date).AddSeconds(15)
    while ((Get-Date) -lt $portDeadline) {
        $listener = Get-NetTCPConnection -LocalPort $HealthPort -State Listen -ErrorAction SilentlyContinue
        if (-not $listener) { break }
        Start-Sleep -Seconds 2
    }

    Start-Process -FilePath $DcPython -ArgumentList '-m', 'backend.workers.market_data_center' -WorkingDirectory $RepoRoot -WindowStyle Hidden
    Write-DcLog ('DC launched (' + $DcPython + ')')

    $deadline = (Get-Date).AddSeconds($GraceAfterRestartSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        if (Test-DcHealth) {
            Write-DcLog 'DC healthy again after restart'
            return
        }
    }
    Write-DcLog 'WARN: DC not healthy after restart'
}


Write-DcLog ('started (port=' + $HealthPort + ' interval=' + $IntervalSec + 's threshold=' + $FailThreshold + ')')
$fail = 0
while ($true) {
    Start-Sleep -Seconds $IntervalSec
    if (Test-DcHealth) {
        if ($fail -gt 0) { Write-DcLog 'DC recovered' }
        $fail = 0
        continue
    }
    $fail++
    $running = @(Get-DcPids).Count
    Write-DcLog ('health check failed (' + $fail + '/' + $FailThreshold + ') dc_processes=' + $running)
    if ($fail -ge $FailThreshold) {
        Restart-Dc
        $fail = 0
    }
}

