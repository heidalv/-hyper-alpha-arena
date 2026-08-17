<#
.SYNOPSIS
    开发环境后端看门狗：8000 不可用/假死时自动 stop + 重启 backend。

.DESCRIPTION
    探测轻量 /api/health。后端假死特征是「端口在听但 HTTP 无响应」。
    旧实现用 Invoke-WebRequest 且超时 30s、连续 8 次才重启，假死期间用户会卡很久；
    且看门狗进程常在 start-dev 之外被杀掉后无人拉起。

    2026-08-09 修复：
      - 用 curl.exe 短超时探测（健康接口本身极轻，>8s 即视为假死）
      - 区分：端口在听但超时 = zombie（阈值更低，更快重启）
      - 连接拒绝 = down（阈值稍高，避免重启抖动）
      - 单实例互斥，避免多个看门狗互相杀进程
      - 重启后二次确认健康；启动失败写日志
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$IntervalSec = 20,
    [int]$FailThresholdDown = 5,
    [int]$FailThresholdZombie = 10,
    [int]$GraceAfterRestartSec = 120,
    [int]$HealthTimeoutSec = 12,
    [int]$GracefulWaitSec = 20,
    [int]$PostRestartCoolSec = 300
)

$ErrorActionPreference = 'SilentlyContinue'
$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path $ScriptDir -Parent
$LogDir = Join-Path $RepoRoot 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$LogFile = Join-Path $LogDir 'backend-watchdog.log'
$LockFile = Join-Path $LogDir 'backend-watchdog.lock'

function Write-WdLog([string]$msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
    Write-Host $line -ForegroundColor DarkYellow
}

# 单实例：已有存活看门狗则退出，避免双狗互殴
try {
    $myPid = $PID
    if (Test-Path $LockFile) {
        $oldPid = 0
        try { $oldPid = [int](Get-Content $LockFile -Raw).Trim() } catch { $oldPid = 0 }
        if ($oldPid -gt 0 -and $oldPid -ne $myPid) {
            $alive = Get-Process -Id $oldPid -ErrorAction SilentlyContinue
            if ($alive) {
                Write-WdLog "[watchdog] another instance already running (pid=$oldPid) — exit"
                exit 0
            }
        }
    }
    Set-Content -Path $LockFile -Value "$myPid" -Encoding ASCII
} catch {
    Write-WdLog "[watchdog] lock warning: $($_.Exception.Message)"
}

function Test-PortListening([int]$port) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    return [bool]$c
}

function Probe-BackendHealth {
    <#
      返回: ok | timeout | refused | error
      优先 curl（超时可靠）；无 curl 时回退 Invoke-WebRequest
    #>
    $uri = "http://127.0.0.1:$BackendPort/api/health"
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if ($curl) {
        $tmp = Join-Path $env:TEMP ("aa-wd-health-{0}.txt" -f $BackendPort)
        try {
            $args = @(
                '-sS', '-o', $tmp, '-w', '%{http_code}',
                '--connect-timeout', '3',
                '--max-time', "$HealthTimeoutSec",
                $uri
            )
            $code = & curl.exe @args 2>$null
            if ($LASTEXITCODE -eq 28 -or $LASTEXITCODE -eq 7) {
                # 28=timeout, 7=failed to connect
                if ($LASTEXITCODE -eq 28) { return 'timeout' }
                return 'refused'
            }
            if ("$code" -eq '200') { return 'ok' }
            if (-not (Test-PortListening $BackendPort)) { return 'refused' }
            return 'error'
        } catch {
            if (Test-PortListening $BackendPort) { return 'timeout' }
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
        if (Test-PortListening $BackendPort) { return 'timeout' }
        return 'refused'
    }
}

function Get-BackendPids {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue
    return @(
        $procs | Where-Object {
            $cl = $_.CommandLine
            if (-not $cl) { return $false }
            ($cl -match 'run_uvicorn_dev\.py') -or
            ($cl -match 'uvicorn' -and $cl -match 'backend\.main:app') -or
            ($cl -match 'uvicorn' -and $cl -match 'backend\.main')
        } | ForEach-Object { $_.ProcessId }
    )
}

function Restart-Backend([string]$reason) {
    Start-Sleep -Seconds 2
    $probe = Probe-BackendHealth
    if ($probe -eq 'ok') {
        Write-WdLog "[watchdog] pre-restart probe OK — skip restart (transient; was $reason)"
        return
    }
    Write-WdLog "[watchdog] backend $reason — stop + start (port $BackendPort) probe=$probe pids=$((Get-BackendPids) -join ',')"

    $pids = Get-BackendPids
    foreach ($procId in $pids) {
        taskkill /PID $procId /T 2>$null | Out-Null
    }
    $deadline = (Get-Date).AddSeconds($GracefulWaitSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-BackendPids)) { break }
        Start-Sleep -Seconds 2
    }

    & (Join-Path $ScriptDir 'stop-dev.ps1') -Ports @($BackendPort, ($BackendPort + 1)) | Out-Null
    Start-Sleep -Seconds 3

    # 看门狗自身继续跑：子启动禁止再起 watchdog，避免套娃
    & (Join-Path $ScriptDir 'start-dev.ps1') -NoFrontend -NoWatchdog | Out-Null

    $okAt = $null
    $deadline = (Get-Date).AddSeconds($GraceAfterRestartSec)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        if ((Probe-BackendHealth) -eq 'ok') {
            $okAt = Get-Date
            break
        }
    }
    if ($okAt) {
        Write-WdLog "[watchdog] backend healthy after restart"
    } else {
        Write-WdLog "[watchdog] WARN: backend still unhealthy after ${GraceAfterRestartSec}s — will keep probing"
    }
}

Write-WdLog "[watchdog] started (port=$BackendPort interval=${IntervalSec}s zombieThreshold=$FailThresholdZombie downThreshold=$FailThresholdDown timeout=${HealthTimeoutSec}s cooldown=${PostRestartCoolSec}s probe=/api/health pid=$PID)"

$fail = 0
$failKind = ''
# [2026-08-16 修复死亡循环] 后端启动后的风暴期（FullAuto 各循环 + 批标注 + 因子
# 闸门 + LLM 预热同时爆发，GIL 饱和）会让极轻的 /api/health 也偶发 >8s 超时。
# 此前 3 次僵尸阈值 ≈60s 恰好短于 3~4 分钟的风暴期 → 每次重启都误杀 → 无限循环。
# 现在：重启后 $PostRestartCoolSec 内 zombie 超时不计数（down 仍计数），冷却期后
# 需连续 $FailThresholdZombie 次超时（≥200s）才判死——真僵尸永不恢复，晚几分钟
# 重启无害；健康但繁忙的后端不再被误杀。
$coolUntil = [datetime]::MinValue
while ($true) {
    Start-Sleep -Seconds $IntervalSec
    $result = Probe-BackendHealth
    if ($result -eq 'ok') {
        if ($fail -gt 0) {
            Write-WdLog "[watchdog] backend recovered (was $fail x $failKind)"
        }
        $fail = 0
        $failKind = ''
        continue
    }

    $listening = Test-PortListening $BackendPort
    $kind = if ($result -eq 'timeout' -or ($listening -and $result -ne 'refused')) { 'zombie' } else { 'down' }
    if ($kind -eq 'zombie' -and (Get-Date) -lt $coolUntil) {
        Write-WdLog "[watchdog] zombie timeout during post-restart cooldown — tolerate (probe=$result listen=$listening)"
        continue
    }
    if ($failKind -ne $kind) {
        # 失败类型切换时重置计数，避免混合计数误伤
        if ($fail -gt 0) {
            Write-WdLog "[watchdog] failure kind changed $failKind -> $kind (reset count)"
        }
        $fail = 0
        $failKind = $kind
    }
    $fail++
    $threshold = if ($kind -eq 'zombie') { $FailThresholdZombie } else { $FailThresholdDown }
    $pids = @(Get-BackendPids)
    Write-WdLog "[watchdog] health $result ($fail/$threshold kind=$kind listen=$listening procs=$($pids.Count))"
    if ($fail -ge $threshold) {
        Restart-Backend $kind
        $fail = 0
        $failKind = ''
        $coolUntil = (Get-Date).AddSeconds($PostRestartCoolSec)
    }
}
