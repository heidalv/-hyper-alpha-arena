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

    # [h768 2026-10-03] **-WindowStyle Hidden 对控制台程序不生效**(PowerShell 已知
    # 行为)⇒ 实测 DC 每 3-5 分钟一轮重启、每轮弹一个黑框。改走
    # start-dc-hidden.vbs(WScript.Shell.Run(...,0) 的 SW_HIDE 对控制台程序有效)。
    # [2026-10-04 工作流④-4 修复 · 实测事故] vbs 启动**不落地**：
    #   logs/data-center-watchdog.log 实测
    #     04:33:14 DC launched via hidden vbs, wscript pid=14572
    #     04:34:46 WARN: DC not healthy after restart
    #     04:35:22 health refused (1/3 kind=down dc_processes=0)   ← 进程从未起来
    #   ⇒ 看门狗"杀了不拉"，净效果就是制造停机。
    # 改为**与 start-dev 相同的直接启动**（已被反复证明有效）：
    #   python.exe -m backend.workers.market_data_center（最小化窗口，牺牲隐藏换可靠性）。
    # 回滚：DC_WATCHDOG_USE_VBS=true 可恢复旧的 vbs 路径。
    $useVbs = ((Get-Item Env:DC_WATCHDOG_USE_VBS -ErrorAction SilentlyContinue).Value -eq 'true')
    if ($useVbs) {
        $dcVbs = Join-Path $RepoRoot 'scripts\start-dc-hidden.vbs'
        $psi = Start-Process -FilePath 'wscript.exe' `
            -ArgumentList '//B', '//Nologo', $dcVbs `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -PassThru
        Write-DcLog ('DC launched via hidden vbs (fallback), wscript pid=' + $psi.Id)
    } else {
        $py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
        if (-not (Test-Path $py)) { $py = 'python' }
        Start-Process -FilePath $py `
            -ArgumentList @('-m', 'backend.workers.market_data_center') `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Minimized
        Write-DcLog ('DC launched directly (python -m backend.workers.market_data_center)')
    }

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

    # [2026-10-04 关键修复 · 实测事故] 禁止仅凭 /health 超时就把"正在采集"的 DC 杀掉。
    # 事故证据（logs/data-center-watchdog.log）：
    #   04:27:46 health timeout (1/2 kind=zombie dc_processes=2)
    #   04:32:01 health timeout (2/2 kind=zombie dc_processes=2)
    #   04:33:03 DC zombie - restarting  pids=32084,31800   ← 当时 DC 正健康（日志持续写入、kline lag=0）
    #   04:33:14 DC launched via hidden vbs → 未健康 → down  ← 它自己的重启不落地
    # 净效果：看门狗反复杀死健康的数据中台，故障伪装成"策略不开仓"（决策全是 DATA_MISSING）。
    # 现在：zombie 判定必须**同时**满足"数据不再流动"（data-center.log 超过 $StaleMin 分钟未写）。
    # 数据仍在流动时只告警、不重启（回滚：把 $StaleMin 设为 0 即恢复旧行为）。
    $logPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'logs\data-center.log'
    # [2026-10-04 修正] 该守护仅适用于 zombie（进程还在、/health 不应答）。
    # 实测缺陷：kind='down'（进程已消失）时日志可能仍"新鲜"几分钟 ⇒ 旧写法会让 DC 一直不被拉起
    # （实测 process_count=0、lag=4m、看门狗不动作，需人工恢复）。
    # [2026-10-08 修复 · 实测僵尸漏判] 旧写法只看「日志 10 分钟内有没有写」，
    # 但日志被事件桥/调度器等着 ⇒ 行情采集卡死时日志仍在写 ⇒ 误判「数据在流」不重启。
    # 改为**直接测行情价格动不动**：查 asterdex_book_ticker 最新一行距现在几秒。
    # 行情 > 90s 没更新 ⇒ 真僵尸（哪怕日志在写），走重启。
    if ($kind -eq 'zombie') {
        $marketStale = $true
        try {
            $py = Join-Path $RepoRoot '.venv\Scripts\python.exe'
            if (-not (Test-Path $py)) { $py = 'python' }
            $probe = & $py -c "import sys,time; sys.path.insert(0,r'$RepoRoot'); from sqlalchemy import text; from backend.database.connection import MarketSessionLocal; db=MarketSessionLocal(); r=db.execute(text('SELECT MAX(event_ts_ms) FROM asterdex_book_ticker')).fetchone(); print(int(time.time()*1000-(r[0] or 0))/1000 if r and r[0] else 99999); db.close()" 2>$null
            $ageSec = 99999
            if ($probe) { [void][int]::TryParse(($probe | Select-Object -Last 1).ToString().Trim(), [ref]$ageSec) }
            if ($ageSec -lt 90) {
                Write-DcLog ('health ' + $result + ' but market data fresh (' + $ageSec + 's) - NOT restarting')
                $fail = 0
                $failKind = ''
                continue
            }
            Write-DcLog ('health ' + $result + ' AND market stale (' + $ageSec + 's) - real zombie')
        } catch {
            Write-DcLog ('market probe failed (' + $_.Exception.Message + ') - treat as stale')
        }
    }
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
