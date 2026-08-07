<#
.SYNOPSIS
    开发环境后端看门狗：8000 连续不可用时自动 stop + 重启 backend（修复 uvicorn reload 僵尸进程）。

.DESCRIPTION
    Windows 上 uvicorn --reload 偶发「旧 worker 已 shutdown 但新 worker 未监听端口」，
    表现为页面全挂、日志里 apscheduler 报 cannot schedule after shutdown。
    本脚本每 IntervalSec 探测轻量 /api/health，连续 FailThreshold 次失败则清理并重启 backend。

    2026-06-17 调参（缓解误杀）：
      - HealthTimeoutSec 15s → 30s：LLM 高峰/GC 时 /api/health 偶发 >15s，导致误判 down
        （backend-watchdog.log 显示最近一天 7 次 stop+start，多数是单次超时抖动）。
      - FailThreshold 5 → 8：从 150s 放宽到约 4 分钟连续失败才重启，给 LLM 长任务恢复时间。
      - GraceAfterRestartSec 90s → 120s：启动后全量恢复（含 fullauto session restore）需更久。
      - Restart-Backend 增加优雅停止窗口：先尝试 graceful（taskkill 不带 /F 给控制台 CTRL_C），
        等 GracefulWaitSec 后仍存活才 Force kill，让 uvicorn lifespan/shutdown_services 有机会跑完。
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$IntervalSec = 30,
    [int]$FailThreshold = 8,
    [int]$GraceAfterRestartSec = 120,
    [int]$HealthTimeoutSec = 30,
    [int]$GracefulWaitSec = 20
)

$ErrorActionPreference = 'SilentlyContinue'
$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path $ScriptDir -Parent
$LogFile = Join-Path $RepoRoot 'logs\backend-watchdog.log'

function Write-WdLog([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line -ForegroundColor DarkYellow
}

function Test-BackendHealth {
    try {
        $uri = "http://127.0.0.1:$BackendPort/api/health"
        $r = Invoke-WebRequest -Uri $uri -TimeoutSec $HealthTimeoutSec -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-BackendPids {
    # 返回 uvicorn 主进程 + reloader 子进程的 PID 列表（与 stop-dev.ps1 同样的过滤口径）
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='cmd.exe'" | Where-Object {
        ($_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'backend\.main:app') -or
        $_.CommandLine -match 'run_uvicorn_dev\.py'
    }
    return @($procs | ForEach-Object { $_.ProcessId })
}

function Restart-Backend {
    # 重启前再探一次，避免 LLM 高峰单次抖动误重启
    Start-Sleep -Seconds 3
    if (Test-BackendHealth) {
        Write-WdLog "[watchdog] pre-restart probe OK — skip restart (transient failure)"
        return
    }
    Write-WdLog "[watchdog] backend down — stop + start (port $BackendPort)"

    # ── 优雅停止窗口（2026-06-17 新增）──
    # stop-dev.ps1 内部全程 Stop-Process -Force，不给 uvicorn 走 lifespan shutdown 的机会，
    # 导致 apscheduler 被强杀时仍在 _process_jobs 抢提交（日志 140 次 cannot schedule）。
    # 这里先用 taskkill 不带 /F 向进程组发 CTRL_C/CTRL_BREAK，让 Python 的 SIGINT handler
    # 触发 uvicorn lifespan → shutdown_services（含 fullauto job 注销 + scheduler.wait=True）。
    $pids = Get-BackendPids
    foreach ($procId in $pids) {
        # /T = 连同子进程；不带 /F = 先尝试优雅信号
        taskkill /PID $procId /T 2>$null | Out-Null
    }
    # 等待优雅关闭窗口
    $deadline = (Get-Date).AddSeconds($GracefulWaitSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-BackendPids)) { break }
        Start-Sleep -Seconds 2
    }

    # 优雅窗口过后仍存活的，走 stop-dev.ps1 强制清理（含端口回收）
    & (Join-Path $ScriptDir 'stop-dev.ps1') -Ports @($BackendPort, ($BackendPort + 1)) | Out-Null
    Start-Sleep -Seconds 3
    & (Join-Path $ScriptDir 'start-dev.ps1') -NoFrontend -NoWatchdog | Out-Null
    Start-Sleep -Seconds $GraceAfterRestartSec
}

Write-WdLog "[watchdog] started (port=$BackendPort interval=${IntervalSec}s threshold=$FailThreshold timeout=${HealthTimeoutSec}s probe=/api/health)"

$fail = 0
while ($true) {
    Start-Sleep -Seconds $IntervalSec
    if (Test-BackendHealth) {
        if ($fail -gt 0) {
            Write-WdLog "[watchdog] backend recovered"
        }
        $fail = 0
        continue
    }
    $fail++
    Write-WdLog "[watchdog] health check failed ($fail/$FailThreshold)"
    if ($fail -ge $FailThreshold) {
        Restart-Backend
        $fail = 0
    }
}
