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
    [int]$FailThreshold = 5,
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
    # [2026-09-11 修复] $HealthTimeoutSec 此前是死参数（WebClient 默认 100s 超时），
    # 探针在 DB 突发负载下挂死 100s×3 才判死；且 3 次失败（~90s）对「慢但活着」
    # 的后端太激进（20:16 实测：8 并发因子预热加载 K 线 → /api/health 变慢 →
    # watchdog 误杀健康后端）。现在真正应用 8s 超时，判死阈值默认 5 次
    # （~2.5 分钟容忍窗口），慢但活着的后端不会被误杀。
    try {
        $wc = New-Object System.Net.WebClient
        $wc.Headers.Add('Accept', 'application/json')
        # WebClient 无直接超时属性：用异步下载 + Wait 实现超时
        $task = $wc.DownloadStringTaskAsync("http://127.0.0.1:$HealthPort/api/config/default-exchange")
        if (-not $task.Wait($HealthTimeoutSec * 1000)) {
            $wc.CancelAsync()
            return $false
        }
        $resp = $task.Result
        return $resp.Length -gt 0
    } catch {
        return $false
    }
}

function Get-BackendPortPid {
    # [2026-10-09 崩溃循环排查] 探针失败时同时记录"端口是否仍被监听+占有者 PID"：
    #   · port_pid>0 ⇒ 后端活着但探针超时（慢，不是死）——不该拉起；
    #   · port_pid=0 ⇒ 后端真死了（被外部终止）——启动器此时才有意义。
    try {
        $c = Get-NetTCPConnection -LocalPort $HealthPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($c) { return [int]$c.OwningProcess }
    } catch { }
    return 0
}

function Start-Backend {
    # [h768 2026-10-03] 不再经 cmd.exe（cmd 一定分配控制台 ⇒ 每次重启弹黑框，
    # 而 -WindowStyle Hidden 对控制台程序不生效）。改走 start-backend-hidden.vbs:
    # WScript.Shell.Run(..., 0) 的 SW_HIDE 对控制台程序有效（run-quiet.vbs 同原理）。
    Write-WdLog "backend down -> starting via start-backend-hidden.vbs (windowless)"
    $vbs = Join-Path $RepoRoot 'scripts\start-backend-hidden.vbs'
    Start-Process -FilePath 'wscript.exe' -ArgumentList '//B', '//Nologo', $vbs -WindowStyle Hidden
}

Write-WdLog "backend watchdog started (port=$HealthPort interval=${IntervalSec}s threshold=${FailThreshold}s)"
$failCount = 0
$lastRestart = 0

while ($true) {
    $ok = Test-BackendHealth
    $portPid = 0
    if (-not $ok) { $portPid = Get-BackendPortPid }
    if ($ok) {
        if ($failCount -ge $FailThreshold) {
            Write-WdLog "backend recovered"
        }
        $failCount = 0
    } else {
        $failCount++
        Write-WdLog "probe fail ($failCount/$FailThreshold) port_pid=$portPid"
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
