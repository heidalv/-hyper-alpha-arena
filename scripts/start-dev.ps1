<#
.SYNOPSIS
    One-shot dev launcher: Data Center → backend (uvicorn) → frontend-next (:5273).

.DESCRIPTION
    1) stop-dev（默认保留独立数据中心）
    2) 确保 standalone Data Center (:9100) 在跑
    3) 启动 backend，注入 DATA_CENTER_MODE=standalone（API 只读行情写）
    4) 启动 frontend-next（Next.js :5273）—— 旧 Vite :5173 已冻结，禁止启动
    5) 等待端口就绪并打印状态

.PARAMETER NoDataCenter
    跳过独立数据中心，沿用 API 内嵌采集（旧行为）

.PARAMETER StopDataCenter
    清理时一并停止数据中心进程
#>
[CmdletBinding()]
param(
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5273,
    [switch]$NoFrontend,
    [switch]$NoBackend,
    [switch]$NoReload,
    [switch]$NoWatchdog,
    [switch]$NoDataCenter,
    [switch]$StopDataCenter
)

$ErrorActionPreference = 'Stop'

# Windows 默认关热重载：reload 子进程常残留占 8000，新进程起不来，前端 API 全挂像「卡死」
if (-not $PSBoundParameters.ContainsKey('NoReload') -and $env:OS -match 'Windows') {
    $NoReload = $true
    Write-Host "[info] Windows 默认 NO_RELOAD（需要热重载请显式: -NoReload:`$false 不支持；请设 `$env:FORCE_RELOAD=1）" -ForegroundColor DarkGray
}
if ($env:FORCE_RELOAD -eq '1') { $NoReload = $false }

$RepoRoot    = Split-Path $PSScriptRoot -Parent
$FrontendDir = Join-Path $RepoRoot 'frontend-next'
$LogDir      = Join-Path $RepoRoot 'logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

if (-not (Test-Path $FrontendDir)) {
    Write-Host "[ERROR] frontend-next 不存在：$FrontendDir" -ForegroundColor Red
    Write-Host "        正式前端是 frontend-next(:5273)。旧 frontend(:5173) 已冻结。" -ForegroundColor Yellow
    exit 1
}

$PyExe = $null
foreach ($cand in @(
    (Join-Path $RepoRoot 'backend\.venv\Scripts\python.exe'),
    (Join-Path $RepoRoot 'backend\venv\Scripts\python.exe'),
    (Join-Path $RepoRoot '.venv\Scripts\python.exe')
)) {
    if (Test-Path $cand) { $PyExe = $cand; break }
}
if (-not $PyExe) {
    Write-Host "[WARN] venv python not found under backend\.venv; falling back to 'python' on PATH." -ForegroundColor Yellow
    $PyExe = 'python'
}

# R1 配置漂移校验（不阻断启动；有漂移/重复键时黄字提示）
$DriftScript = Join-Path $RepoRoot 'scripts\check_config_drift.py'
if (Test-Path $DriftScript) {
    & $PyExe $DriftScript *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[drift] config OK (no drift / no duplicate keys)" -ForegroundColor DarkGray
    } else {
        Write-Host "[drift] WARNING: config drift or duplicate keys found. Run: python scripts\check_config_drift.py --verbose" -ForegroundColor Yellow
    }
}

$BackendLog  = Join-Path $LogDir 'backend.log'
$FrontendLog = Join-Path $LogDir 'frontend-next.log'
$DataCenterLog = Join-Path $LogDir 'data-center.log'
$DataCenterHealthPort = 9100

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Hyper-Alpha-Arena dev launcher" -ForegroundColor Cyan
Write-Host " frontend = frontend-next :$FrontendPort" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

function Test-Port($p) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue
    if ($c) { return $c.OwningProcess | Select-Object -First 1 } else { return $null }
}

function Test-DataCenterHealthy {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$DataCenterHealthPort/health" -UseBasicParsing -TimeoutSec 2
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

Write-Host "`n[1/4] cleaning old API/frontend (keep data-center unless -StopDataCenter)..." -ForegroundColor Yellow
# 顺带清掉误起的旧 Vite 5173
# [2026-08-15] -NoFrontend 时不得清理 5273：看门狗只重启后端，不能拖垮正在服务的前端。
if ($NoFrontend) {
    $stopArgs = @{ Ports = @($BackendPort, ($BackendPort + 1)) }
} else {
    $stopArgs = @{ Ports = @($BackendPort, ($BackendPort + 1), $FrontendPort, 5173, 5174, 5175) }
}
if ($StopDataCenter) { $stopArgs['StopDataCenter'] = $true }
& (Join-Path $PSScriptRoot 'stop-dev.ps1') @stopArgs | Out-Host

$useStandaloneDc = -not $NoDataCenter
if ($useStandaloneDc) {
    Write-Host "`n[2/4] ensuring standalone Data Center on :$DataCenterHealthPort ..." -ForegroundColor Yellow
    if (Test-DataCenterHealthy) {
        Write-Host "   already healthy" -ForegroundColor Green
    } else {
        $dcProc = Start-Process -FilePath $PyExe `
            -ArgumentList @('-m', 'backend.workers.market_data_center') `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Minimized `
            -PassThru
        Write-Host "   started pid=$($dcProc.Id)  log=$DataCenterLog" -ForegroundColor Green
        $dcReady = $false
        for ($i = 0; $i -lt 45; $i++) {
            Start-Sleep -Seconds 1
            if (Test-DataCenterHealthy) { $dcReady = $true; break }
            if (($i % 5) -eq 4) {
                Write-Host "   waiting data-center health... +$($i+1)s" -ForegroundColor DarkGray
            }
        }
        if ($dcReady) {
            Write-Host "   [OK] http://127.0.0.1:$DataCenterHealthPort/health" -ForegroundColor Green
        } else {
            Write-Host "   [WARN] not healthy yet; check $DataCenterLog" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "`n[2/4] Data Center skipped (-NoDataCenter) → embedded collectors" -ForegroundColor DarkGray
}

if (-not $NoBackend) {
    $reloadTag = if ($NoReload) { '(no reload)' } else { '(reload via run_uvicorn_dev.py)' }
    Write-Host "`n[3/4] starting uvicorn on :$BackendPort $reloadTag ..." -ForegroundColor Yellow
    Write-Host "   python: $PyExe" -ForegroundColor DarkGray
    $runner = Join-Path $PSScriptRoot 'run_uvicorn_dev.py'
    $env:BACKEND_PORT = "$BackendPort"
    # [2026-08-05 远程访问] 后端监听所有网卡（Tailscale 虚拟网卡 + 局域网），
    # 供外地电脑经 Tailscale 虚拟 IP 访问；本机依然可用 127.0.0.1。
    $env:BACKEND_HOST = '0.0.0.0'
    if ($NoReload) { $env:NO_RELOAD = 'true' } else { Remove-Item Env:NO_RELOAD -ErrorAction SilentlyContinue }

    if ($useStandaloneDc) {
        Write-Host "   DATA_CENTER_MODE=standalone" -ForegroundColor DarkGray
        # [fix] 单引号字符串：cmd 的 && 与路径引号全部按字面量传给 cmd.exe，
        # 避免 Windows PowerShell 5.1 把 `&& "` 误解析为运算符导致脚本无法运行。
        # 注意：不要把 stdout 重定向到 backend.log / uvicorn-stdout.log——
        # Windows 下 uvicorn --reload 子进程继承句柄后容易 PermissionError / 起不来。
        $backendCmd = 'set DATA_CENTER_MODE=standalone&& "' + $PyExe + '" "' + $runner + '"'
        $bp = Start-Process -FilePath 'cmd.exe' `
            -ArgumentList '/c', $backendCmd `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden -PassThru
    } else {
        $bp = Start-Process -FilePath $PyExe `
            -ArgumentList "`"$runner`"" `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden -PassThru
    }
    $reloadNote = if ($NoReload) { 'NO_RELOAD=true（单进程，改 .py 需手动重启）' } else { 'reload ON（改 .py 自动重启）' }
    Write-Host "   launcher_pid=$($bp.Id)  log=$BackendLog ($reloadNote)" -ForegroundColor Green
}

if (-not $NoFrontend) {
    Write-Host "`n[4/4] starting frontend-next on :$FrontendPort ..." -ForegroundColor Yellow
    if (-not (Test-Path (Join-Path $FrontendDir 'node_modules'))) {
        Write-Host "   [WARN] frontend-next/node_modules 缺失，请先: cd frontend-next && npm install" -ForegroundColor Yellow
    }
    # 若已在听，不重复起
    if (Test-Port $FrontendPort) {
        Write-Host "   already listening on :$FrontendPort" -ForegroundColor Green
    } else {
        $frontCmd = "npm run dev > `"$FrontendLog`" 2>&1"
        $fp = Start-Process -FilePath 'cmd.exe' `
            -ArgumentList '/c', $frontCmd `
            -WorkingDirectory $FrontendDir `
            -WindowStyle Hidden -PassThru
        Write-Host "   pid=$($fp.Id)  log=$FrontendLog" -ForegroundColor Green
    }
}

$WaitTotalSec = 90
$StepSec = 1
Write-Host "`nwaiting up to $WaitTotalSec s for services to listen..." -ForegroundColor DarkGray

$backendReady = $NoBackend
$frontendReady = $NoFrontend
$frontendPortFound = $null
for ($i = 0; $i -lt $WaitTotalSec; $i += $StepSec) {
    if (-not $backendReady -and (Test-Port $BackendPort)) { $backendReady = $true }
    if (-not $frontendReady) {
        if (Test-Port $FrontendPort) {
            $frontendReady = $true
            $frontendPortFound = $FrontendPort
        }
    }
    if ($backendReady -and $frontendReady) { break }
    Start-Sleep -Seconds $StepSec
    if ($i -gt 0 -and ($i % 5) -eq 0) {
        $b = if ($backendReady) { 'OK' } else { 'waiting' }
        $f = if ($frontendReady) { 'OK' } else { 'waiting' }
        Write-Host "  [+${i}s] backend=$b frontend=$f" -ForegroundColor DarkGray
    }
}

Write-Host "`n========== final status ==========" -ForegroundColor Cyan

if ($useStandaloneDc) {
    if (Test-DataCenterHealthy) {
        Write-Host "  [OK  ] data-center -> http://127.0.0.1:$DataCenterHealthPort/health" -ForegroundColor Green
    } else {
        Write-Host "  [WAIT] data-center -> check $DataCenterLog" -ForegroundColor Yellow
    }
}
if (-not $NoBackend) {
    $pidB = Test-Port $BackendPort
    $healthOk = $false
    $healthErr = $null
    try {
        $hr = Invoke-WebRequest -Uri "http://127.0.0.1:$BackendPort/api/health" -UseBasicParsing -TimeoutSec 3
        $healthOk = ($hr.StatusCode -eq 200)
    } catch { $healthErr = $_.Exception.Message }
    $ownerCmd = $null
    if ($pidB) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId=$pidB" -ErrorAction SilentlyContinue
        $ownerCmd = $owner.CommandLine
    }
    $ours = $ownerCmd -and (
        $ownerCmd -match 'run_uvicorn_dev|uvicorn.*backend\.main|Hyper-Alpha-Arena'
    )
    if ($pidB -and $healthOk -and ($ours -or -not $ownerCmd)) {
        Write-Host "  [OK  ] backend  -> http://127.0.0.1:$BackendPort  (pid=$pidB, health=ok)" -ForegroundColor Green
        if (-not $ownerCmd) {
            Write-Host "         [WARN] listener pid=$pidB 进程表缺失（幽灵端口风险）；若 API 异常请 stop-dev 后再起" -ForegroundColor Yellow
        }
    } elseif ($pidB -and -not $ours -and $ownerCmd) {
        Write-Host "  [FAIL] backend  -> :$BackendPort 被非本项目进程占用 pid=$pidB" -ForegroundColor Red
        Write-Host "         $ownerCmd" -ForegroundColor DarkGray
        Write-Host "         请先: .\scripts\stop-dev.ps1  再重新 start-dev" -ForegroundColor Yellow
    } elseif ($pidB -and -not $healthOk) {
        Write-Host "  [FAIL] backend  -> 端口在听但 /api/health 失败 (pid=$pidB): $healthErr" -ForegroundColor Red
        Write-Host "         多为旧僵尸占口；请 stop-dev 后重试。log=$BackendLog" -ForegroundColor Yellow
    } else {
        Write-Host "  [WAIT] backend  -> port $BackendPort still not listening after $WaitTotalSec s" -ForegroundColor Yellow
        Write-Host "         check $BackendLog" -ForegroundColor Yellow
    }
}
if (-not $NoFrontend) {
    if ($frontendPortFound) {
        Write-Host "  [OK  ] frontend -> http://127.0.0.1:$frontendPortFound/login  (frontend-next)" -ForegroundColor Green
    } else {
        Write-Host "  [FAIL] frontend -> :$FrontendPort not listening, see $FrontendLog" -ForegroundColor Red
    }
}

Write-Host "`nTip: 正式前端是 :5273 (frontend-next)。旧 Vite :5173 已冻结。" -ForegroundColor DarkGray
Write-Host "Tip: stop-dev 默认保留数据中心；要一起停加 -StopDataCenter" -ForegroundColor DarkGray

if (-not $NoWatchdog) {
    # 清掉旧看门狗（powershell / pwsh / 外包 cmd）
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe' OR Name='pwsh.exe' OR Name='cmd.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and ($_.CommandLine -match 'backend-watchdog\.ps1|data-center-watchdog\.ps1') } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 400
    Remove-Item (Join-Path $LogDir 'backend-watchdog.lock') -Force -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $LogDir 'data-center-watchdog.lock') -Force -ErrorAction SilentlyContinue

    if (-not $NoBackend) {
        $wdScript = Join-Path $PSScriptRoot 'backend-watchdog.ps1'
        $wdLog = Join-Path $LogDir 'backend-watchdog.log'
        Write-Host "`n[watchdog] starting backend watchdog -> $wdLog" -ForegroundColor DarkGray
        Start-Process -FilePath 'powershell.exe' `
            -ArgumentList @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', $wdScript,
                '-BackendPort', "$BackendPort"
            ) `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden | Out-Null
    }

    if ($useStandaloneDc) {
        $dcWd = Join-Path $PSScriptRoot 'data-center-watchdog.ps1'
        $dcWdLog = Join-Path $LogDir 'data-center-watchdog.log'
        Write-Host "[watchdog] starting data-center watchdog -> $dcWdLog" -ForegroundColor DarkGray
        Start-Process -FilePath 'powershell.exe' `
            -ArgumentList @(
                '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-File', $dcWd,
                '-HealthPort', "$DataCenterHealthPort",
                '-PythonExe', $PyExe
            ) `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden | Out-Null
    }
}
