<#
.SYNOPSIS
    Precisely stop Hyper-Alpha-Arena's own uvicorn backend + vite frontend,
    without killing Cursor / IDE / unrelated python / unrelated node.

.DESCRIPTION
    Filter rules (BOTH conditions must match before a PID is killed):
      - python.exe : CommandLine contains "uvicorn" AND "backend.main:app"
      - node.exe   : CommandLine contains "vite" or "npm run dev", AND also
                     references "Hyper-Alpha-Arena"

    The old stop.ps1 used ``Stop-Process -Name node -Force`` which will also
    kill Cursor's own TypeScript server / Pyright LSP / autopep8 / etc.
    NEVER use that one again - run this script instead.

    Also reclaims ports 5173/5174/5175/8000/8001 by default.

.PARAMETER Ports
    Extra TCP ports to release. Default: 5173,5174,5175,8000,8001

.PARAMETER DryRun
    Only print which PIDs would be killed.

.EXAMPLE
    .\scripts\stop-dev.ps1
    .\scripts\stop-dev.ps1 -DryRun
#>
[CmdletBinding()]
param(
    # 5273 = 正式 frontend-next；5173/5174/5175 = 旧 Vite（已冻结，仍清理防止误起残留）
    [int[]]$Ports = @(5273, 5173, 5174, 5175, 8000, 8001),
    [switch]$DryRun,
    # 默认保留独立数据中心；显式传入才停
    [switch]$StopDataCenter
)

$ErrorActionPreference = 'SilentlyContinue'

function Write-Step($msg, $color = 'Cyan') {
    Write-Host ("`n==> " + $msg) -ForegroundColor $color
}

function Stop-Matched {
    param(
        [string]$Name,
        [scriptblock]$Filter,
        [string]$Label
    )
    $procs = Get-CimInstance Win32_Process -Filter "Name='$Name'" | Where-Object $Filter
    if (-not $procs) {
        Write-Host "  [SKIP] no $Label found" -ForegroundColor DarkGray
        return 0
    }
    $killed = 0
    foreach ($p in $procs) {
        $cmd = $p.CommandLine
        if ($cmd.Length -gt 130) { $cmd = $cmd.Substring(0, 127) + '...' }
        if ($DryRun) {
            Write-Host "  [DRY ] would kill pid=$($p.ProcessId) $Label -> $cmd" -ForegroundColor Yellow
        } else {
            Write-Host "  [KILL] pid=$($p.ProcessId) $Label -> $cmd" -ForegroundColor Yellow
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
            $killed++
        }
    }
    return $killed
}

Write-Step "1) stop uvicorn backend"
# Reloader master process: "python -m uvicorn backend.main:app --reload ..."
$backendFilter = {
    ($_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'backend\.main:app') -or
    $_.CommandLine -match 'run_uvicorn_dev\.py' -or
    $_.CommandLine -match 'start_server\.py'
}

# --- (1a) kill cmd.exe wrappers that launch uvicorn (Start-Process / manual terminals) ---
$cmdWrapperFilter = {
    ($_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'backend\.main:app') -or
    $_.CommandLine -match 'run_uvicorn_dev\.py' -or
    $_.CommandLine -match 'start_server\.py'
}
$nCmd = Stop-Matched -Name 'cmd.exe' -Filter $cmdWrapperFilter -Label 'uvicorn cmd wrapper'

# --- (1b) find reloader masters first ---
$reloaderProcs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object $backendFilter
$reloaderPids  = @($reloaderProcs | ForEach-Object { $_.ProcessId })

# --- (1b) also find worker children ---
# In uvicorn --reload mode on Windows, the actual ASGI worker is spawned as:
#   python -c "from multiprocessing.spawn import spawn_main; spawn_main(parent_pid=<uvicorn_pid>, pipe_handle=...)"
# Its CommandLine contains no "uvicorn" keyword, so the main filter misses it.
# If we only kill the reloader, the worker keeps listening and squats the port.
# Strategy: kill any python process whose parent is our reloader, OR whose
# CommandLine embeds parent_pid=<one-of-our-reloader-pids>.
$workerFilter = {
    $pp = $_.ParentProcessId
    $cmd = $_.CommandLine
    # already identified as uvicorn reloader -> skip, will be killed below anyway
    if ($cmd -match 'uvicorn' -and $cmd -match 'backend\.main:app') { return $false }
    if (-not $cmd) { return $false }
    if ($cmd -notmatch 'multiprocessing' -and $cmd -notmatch 'spawn_main') { return $false }
    if ($reloaderPids -contains $pp) { return $true }
    foreach ($rpid in $reloaderPids) {
        if ($cmd -match "parent_pid=$rpid(\D|$)") { return $true }
    }
    return $false
}
$n1w = Stop-Matched -Name 'python.exe' -Filter $workerFilter -Label 'uvicorn worker (spawn)'

# --- (1b2) orphan spawn workers squatting backend ports (parent reloader already dead) ---
$backendPorts = @(8000, 8001)
foreach ($port in $backendPorts) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($c in $conns) {
        $pid_ = $c.OwningProcess
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pid_" -ErrorAction SilentlyContinue
        if (-not $proc) { continue }
        $cmd = $proc.CommandLine
        if ($proc.Name -ne 'python.exe') { continue }
        $isSpawn = $cmd -match 'spawn_main|multiprocessing'
        $isUvicorn = $cmd -match 'uvicorn' -and $cmd -match 'backend\.main:app'
        $isRunner = $cmd -match 'run_uvicorn_dev\.py'
        if (-not ($isSpawn -or $isUvicorn -or $isRunner)) { continue }
        if ($DryRun) {
            Write-Host "  [DRY ] orphan on port $port -> kill pid=$pid_" -ForegroundColor Yellow
        } else {
            Write-Host "  [KILL] orphan on port $port -> pid=$pid_ ($($cmd.Substring(0, [Math]::Min(80, $cmd.Length)))...)" -ForegroundColor Yellow
            Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
            $n1w++
        }
    }
}

# --- (1c) then kill the reloaders themselves ---
$n1  = Stop-Matched -Name 'python.exe' -Filter $backendFilter -Label 'uvicorn backend'
$n1  = $n1 + $n1w + $nCmd

Write-Step "1.5) stop desktop launcher (pywebview)"
# Desktop mode: pythonw "desktop\launcher.py" + pywebview window + in-process uvicorn.
# Match both python.exe and pythonw.exe that run launcher.py from this project.
$launcherFilter = {
    $_.CommandLine -match 'desktop[\\/]launcher\.py' -or
    $_.CommandLine -match 'launcher\.py.*--restart-backend'
}
$nLauncherPy = Stop-Matched -Name 'python.exe'  -Filter $launcherFilter -Label 'desktop launcher (python)'
$nLauncherPw = Stop-Matched -Name 'pythonw.exe' -Filter $launcherFilter -Label 'desktop launcher (pythonw)'

# Remove stale pid lock so next launch doesn't falsely abort.
$pidLock = Join-Path (Split-Path $PSScriptRoot -Parent) 'desktop\.alpha-arena.pid'
if (Test-Path $pidLock) {
    if ($DryRun) {
        Write-Host "  [DRY ] would remove stale pid lock: $pidLock" -ForegroundColor Yellow
    } else {
        Remove-Item $pidLock -Force -ErrorAction SilentlyContinue
        Write-Host "  [OK  ] removed stale pid lock: desktop\.alpha-arena.pid" -ForegroundColor Green
    }
}

Write-Step "2) stop frontend-next (:5273) + leftover frozen vite (:5173)"
# 正式前端 Next.js
$nextFilter = {
    $_.CommandLine -match 'Hyper-Alpha-Arena' -and (
        $_.CommandLine -match 'frontend-next' -or
        ($_.CommandLine -match 'next' -and $_.CommandLine -match '5273')
    )
}
$n2next = Stop-Matched -Name 'node.exe' -Filter $nextFilter -Label 'frontend-next'
# 旧 Vite（已冻结）残留也清掉
$viteFilter = {
    $_.CommandLine -match 'Hyper-Alpha-Arena' -and (
        $_.CommandLine -match 'vite' -or
        ($_.CommandLine -match 'npm.*run.*dev' -and $_.CommandLine -match '\\frontend\\')
    )
}
$n2vite = Stop-Matched -Name 'node.exe' -Filter $viteFilter -Label 'frozen vite :5173'
$n2 = $n2next + $n2vite

Write-Step "3) free listening ports ($($Ports -join ', '))"
$extraKilled = 0
foreach ($port in $Ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $conns) {
        Write-Host "  port $port : free" -ForegroundColor DarkGray
        continue
    }
    foreach ($c in $conns) {
        $pid_ = $c.OwningProcess
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pid_"
        if (-not $proc) { continue }
        # "ours" = uvicorn reloader, orphan uvicorn worker (spawn_main), or vite.
        # spawn_main workers are only treated as ours when squatting our backend ports (8000/8001).
        $isOurs = ($proc.Name -eq 'python.exe' -and $proc.CommandLine -match 'uvicorn') -or
                  ($proc.Name -eq 'python.exe' -and $proc.CommandLine -match 'run_uvicorn_dev\.py' -and $port -in 8000, 8001) -or
                  ($proc.Name -eq 'python.exe' -and $proc.CommandLine -match 'Hyper-Alpha-Arena' -and $port -in 8000, 8001) -or
                  ($proc.Name -eq 'python.exe' -and $proc.CommandLine -match 'spawn_main' -and $port -in 8000, 8001) -or
                  ($proc.Name -eq 'node.exe'   -and $proc.CommandLine -match 'vite') -or
                  ($proc.Name -eq 'node.exe'   -and $port -eq 5273 -and ($proc.CommandLine -match 'next|frontend-next'))
        if (-not $isOurs) {
            Write-Host "  port $port -> pid=$pid_ $($proc.Name) (not ours, skip)" -ForegroundColor DarkGray
            continue
        }
        if ($DryRun) {
            Write-Host "  [DRY ] port $port -> kill pid=$pid_ $($proc.Name)" -ForegroundColor Yellow
        } else {
            Write-Host "  [KILL] port $port -> pid=$pid_ $($proc.Name)" -ForegroundColor Yellow
            Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
            $extraKilled++
        }
    }
}

Start-Sleep -Milliseconds 400

Write-Step "4) final check" 'Green'
$aliveBack    = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object $backendFilter
# Orphan workers whose parent (reloader) is already dead may still be alive.
# Detect them by CommandLine containing parent_pid=<known reloader pid> OR by
# listening on backend ports.
$aliveWorkers = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object {
        $cmd = $_.CommandLine
        if (-not $cmd) { return $false }
        if ($cmd -notmatch 'spawn_main') { return $false }
        foreach ($rpid in $reloaderPids) {
            if ($cmd -match "parent_pid=$rpid(\D|$)") { return $true }
        }
        return $false
    })
$aliveNext = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object $nextFilter)
$aliveVite = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object $viteFilter)
$aliveFront = @($aliveNext) + @($aliveVite)
if ($aliveBack -or $aliveWorkers.Count -gt 0) {
    $allPids = @(
        @($aliveBack    | ForEach-Object { $_.ProcessId })
        @($aliveWorkers | ForEach-Object { $_.ProcessId })
    ) -join ','
    Write-Host "  [WARN] uvicorn still alive: pid=$allPids (killing forcefully)" -ForegroundColor Red
    # second-pass hard kill for stubborn orphans
    foreach ($zp in @($aliveBack) + $aliveWorkers) {
        Stop-Process -Id $zp.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Milliseconds 300
} else {
    Write-Host "  [OK  ] uvicorn fully stopped" -ForegroundColor Green
}
if ($aliveFront.Count -gt 0) {
    Write-Host "  [WARN] frontend still alive: pid=$($aliveFront.ProcessId -join ',')" -ForegroundColor Red
} else {
    Write-Host "  [OK  ] frontend-next / frozen-vite fully stopped" -ForegroundColor Green
}

foreach ($port in $Ports) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if (-not $c) { continue }
    $pids = @($c | ForEach-Object { $_.OwningProcess } | Select-Object -Unique)
    Write-Host "  [WARN] port $port still in use (pid=$($pids -join ','))" -ForegroundColor Red
    if ($port -in 8000, 8001) {
        foreach ($pid_ in $pids) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pid_" -ErrorAction SilentlyContinue
            if (-not $proc -or $proc.Name -ne 'python.exe') { continue }
            $cmd = $proc.CommandLine
            if ($cmd -match 'run_uvicorn_dev|uvicorn|spawn_main|multiprocessing') {
                if ($DryRun) {
                    Write-Host "  [DRY ] force-kill backend squatter pid=$pid_" -ForegroundColor Yellow
                } else {
                    Write-Host "  [KILL] force-kill backend squatter pid=$pid_" -ForegroundColor Yellow
                    Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
}

Write-Host ""
if ($StopDataCenter) {
    Write-Step "stop standalone data-center (market_data_center)"
    $nDc = Stop-Matched -Name 'python.exe' -Filter {
        $_.CommandLine -match 'backend\.workers\.market_data_center' -or
        $_.CommandLine -match 'workers\\market_data_center'
    } -Label 'data-center worker'
    # health port 9100
    $c9100 = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue
    if ($c9100) {
        foreach ($pid_ in @($c9100 | ForEach-Object { $_.OwningProcess } | Select-Object -Unique)) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pid_" -ErrorAction SilentlyContinue
            if ($proc -and $proc.CommandLine -match 'market_data_center') {
                if ($DryRun) {
                    Write-Host "  [DRY ] would kill data-center pid=$pid_" -ForegroundColor Yellow
                } else {
                    Write-Host "  [KILL] data-center pid=$pid_" -ForegroundColor Yellow
                    Stop-Process -Id $pid_ -Force -ErrorAction SilentlyContinue
                }
            }
        }
    }
    Write-Host "  data-center stop attempted (matched=$nDc)" -ForegroundColor DarkGray
} else {
    Write-Host "==> data-center kept running (pass -StopDataCenter to kill)" -ForegroundColor DarkGray
}

Write-Host ""
Write-Host ("done. uvicorn killed=$n1, frontend killed=$n2, by-port killed=$extraKilled") -ForegroundColor Cyan
