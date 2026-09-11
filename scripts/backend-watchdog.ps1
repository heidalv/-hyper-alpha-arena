<#
.SYNOPSIS
    后端看门狗：8000 不可用时自动拉起后端（NO_RELOAD 启动器），与 data-center-watchdog 同模式。
.DESCRIPTION
    每 30s 探测 /api/config/default-exchange；连续 3 次失败 → Start-Process 启动
    scripts\start-backend-noreload.cmd；重启后 90s 内不重复拉起（避免连续误判循环）。
    单实例锁：logs\backend-watchdog.lock
#>
[CmdletBinding()]
param(
    [int]$HealthPort = 8000,
    [int]$IntervalSec = 30,
    [int]$FailThreshold = 3,
    [int]$HealthTimeoutSec = 8,
    [int]$GraceAfterRestartSec = 90
)

$ErrorActionPreference = 'SilentlyContinue'
$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path $ScriptDir -Parent
$LogDir = Join-Path $RepoRoot 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$LogFile = Join-Path $LogDir 'backend-watchdog.log'
$LockFile = Join-Path $LogDir 'backend-watchdog.lock'

function Write-WdLog([string]$msg) {
    $line = '{0} [backend-watchdog] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
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
                Write-WdLog "another instance already running (pid=$oldPid) - exit"
                exit 0
            }
        }
    }
    Set-Content -Path $LockFile -Value "$myPid" -Encoding ASCII
} catch {
    Write-WdLog ("lock warning: " + $_.Exception.Message)
}

function Test-BackendHealth {
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add('Accept', 'application/json')
        $resp = $wc.DownloadString("http://127.0.0.1:$HealthPort/api/config/default-exchange")
        return $resp.Length -gt 0
    } catch {
        return $false
    }
}

function Start-Backend {
    Write-WdLog "backend down -> starting via start-backend-noreload.cmd"
    $startCmd = Join-Path $RepoRoot 'scripts\start-backend-noreload.cmd'
    Start-Process -FilePath 'cmd.exe' -ArgumentList "/c `"$startCmd`"" -WindowStyle Hidden
}

Write-WdLog "backend watchdog started (port=$HealthPort interval=${IntervalSec}s threshold=${FailThreshold}s)"
$failCount = 0
$lastRestart = 0

while ($true) {
    $ok = Test-BackendHealth
    if ($ok) {
        if ($failCount -ge $FailThreshold) {
            Write-WdLog "backend recovered"
        }
        $failCount = 0
    } else {
        $failCount++
        Write-WdLog "probe fail ($failCount/$FailThreshold)"
        if ($failCount -ge $FailThreshold) {
            $now = [datetime]::Now
            if (($now - [datetime]::FromFileTime($lastRestart)).TotalSeconds -ge $GraceAfterRestartSec) {
                Start-Backend
                $lastRestart = $now.ToFileTime()
                $failCount = 0
                Start-Sleep -Seconds 10
            }
        }
    }
    Start-Sleep -Seconds $IntervalSec
}
