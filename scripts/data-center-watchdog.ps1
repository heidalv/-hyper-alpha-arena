<#
.SYNOPSIS
    独立数据中心看门狗：9100 不可用时自动重启 market_data_center。
#>
[CmdletBinding()]
param(
    [int]$HealthPort = 9100,
    [int]$IntervalSec = 30,
    [int]$FailThreshold = 3,
    [int]$FailThresholdZombie = 2,
    [int]$HealthTimeoutSec = 8,
    [int]$GraceAfterRestartSec = 90,
    [string]$PythonExe = ''
)

$ErrorActionPreference = 'SilentlyContinue'
$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path $ScriptDir -Parent
$LogDir = Join-Path $RepoRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir 'data-center-watchdog.log'
$LockFile = Join-Path $LogDir 'data-center-watchdog.lock'

function Write-DcLog([string]$msg) {
    $line = '{0} [dc-watchdog] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line -ForegroundColor DarkCyan
}

try {
    $myPid = $PID
    if (Test-Path $LockFile) {
        $oldPid = 0
        try { $oldPid = [int](Get-Content $LockFile -Raw).Trim() } catch { $oldPid = 0 }
        if ($oldPid -gt 0 -and $oldPid -ne $myPid) {
            $alive = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($alive) {
                Write-DcLog "another instance already running (pid=$oldPid) - exit"
                exit 0
            }
        }
    }
    Set-Content -Path $LockFile -Value "$myPid" -Encoding ASCII
} catch {
    Write-DcLog ("lock warning: " + $_.Exception.Message)
}

function Resolve-DcPython {
    if ($PythonExe -and (Test-Path $PythonExe)) {
        return $PythonExe
    }
    $candidates = @(
        (Join-Path $RepoRoot '.venv\Scripts\python.exe'),
        (Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'),
        (Join-Path $RepoRoot '.runtime\Python312\python.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    return 'python'
}
$DcPython = Resolve-DcPython
Write-DcLog ('using python: ' + $DcPython)

function Test-PortListening([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}

function Probe-DcHealth {
    $uri = 'http://127.0.0.1:' + $HealthPort + '/health'
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $tmp = Join-Path $env:TEMP ("aa-dc-health-{0}.txt" -f $HealthPort)
        try {
            $code = & curl.exe -sS -o $tmp -w '%{http_code}' --connect-timeout 3 --max-time $HealthTimeoutSec $uri 2>$null
            if ($LASTEXITCODE -eq 28) { return 'timeout' }
            if ($LASTEXITCODE -eq 7) { return 'refused' }
            if ("$code" -eq '200') { return 'ok' }
            if (-not (Test-PortListening $HealthPort)) { return 'refused' }
            return 'error'
        } catch {
            if (Test-PortListening $HealthPort) { return 'timeout' }
            return 'refused'
        } finally {
            Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        }
    }
    try {
        $r = Invoke-WebRequest -Uri $uri -TimeoutSec $HealthTimeoutSec -UseBasicParsing
        if ($r.StatusCode -eq 200) { return 'ok' }
        return 'error'
    } catch {
        if (Test-PortListening $HealthPort) { return 'timeout' }
        return 'refused'
    }
}

function Get-DcPids {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue
    return @($procs | Where-Object { $_.CommandLine -and ($_.CommandLine -match 'backend\.workers\.market_data_center') } | ForEach-Object { $_.ProcessId })
}

function Restart-Dc([string]$reason) {
    Start-Sleep -Seconds 3
    if ((Probe-DcHealth) -eq 'ok') {
        Write-DcLog 'pre-restart probe OK - skip restart'
        return
    }
    Write-DcLog ('DC ' + $reason + ' - restarting (port ' + $HealthPort + ') pids=' + ((Get-DcPids) -join ','))

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
        if (-not (Test-PortListening $HealthPort)) { break }
        Start-Sleep -Seconds 2
    }

    $envBlock = @{
        DATA_CENTER_MODE = 'standalone'
        DATA_CENTER_PROCESS = '1'
    }
    $psi = Start-Process -FilePath $DcPython `
        -ArgumentList '-m', 'backend.workers.market_data_center' `
        -WorkingDirectory $RepoRoot `
        -WindowStyle Hidden `
        -PassThru
    Write-DcLog ('DC launched pid=' + $psi.Id + ' (' + $DcPython + ')')

    $deadline = (Get-Date).AddSeconds($GraceAfterRestartSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        if ((Probe-DcHealth) -eq 'ok') {
            Write-DcLog 'DC healthy again after restart'
            return
        }
    }
    Write-DcLog 'WARN: DC not healthy after restart'
}

Write-DcLog ('started (port=' + $HealthPort + ' interval=' + $IntervalSec + 's threshold=' + $FailThreshold + ' zombie=' + $FailThresholdZombie + ' pid=' + $PID + ')')
$fail = 0
$failKind = ''
while ($true) {
    Start-Sleep -Seconds $IntervalSec
    $result = Probe-DcHealth
    if ($result -eq 'ok') {
        if ($fail -gt 0) { Write-DcLog ('DC recovered (was ' + $fail + ' x ' + $failKind + ')') }
        $fail = 0
        $failKind = ''
        continue
    }
    $listening = Test-PortListening $HealthPort
    $kind = if ($result -eq 'timeout' -or ($listening -and $result -ne 'refused')) { 'zombie' } else { 'down' }
    if ($failKind -ne $kind) {
        $fail = 0
        $failKind = $kind
    }
    $fail++
    $threshold = if ($kind -eq 'zombie') { $FailThresholdZombie } else { $FailThreshold }
    $running = @(Get-DcPids).Count
    Write-DcLog ('health ' + $result + ' (' + $fail + '/' + $threshold + ' kind=' + $kind + ' dc_processes=' + $running + ')')
    if ($fail -ge $threshold) {
        Restart-Dc $kind
        $fail = 0
        $failKind = ''
    }
}
