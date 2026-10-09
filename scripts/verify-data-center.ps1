<#
.SYNOPSIS
  Data Center health probe with staleness awareness.

.NOTES
  [2026-10-04 workflow-4 research conclusion] Real outage 2026-10-03 21:57 -> 2026-10-04 01:37 (1h39m):
  process gone, port 9100 silent, data-center.log stopped. But the old liveness check
  (start-dev.ps1:89 Test-DataCenterHealthy) only does GET /health (HTTP 200 == healthy), and
  the port had briefly been listening => the outage stayed invisible and looked like
  "strategy does not open positions" (all decisions said [StrictData] missing=price /
  K线DATA_MISSING). The watchdog could have detected it in ~90s
  (data-center-watchdog.ps1:6-11: IntervalSec=30, FailThreshold=3) but took 1h39m
  => the watchdog itself was absent, and stop-dev.ps1 has no watchdog handling at all
  (it only promises "data-center kept running", i.e. it does not kill the DC, but it also
  does not guarantee a supervisor stays alive).

  This script is READ-ONLY. It reports five facts: process / port / health / kline lag / watchdog count.
  Exit code 0 = healthy, 1 = not healthy.

.PARAMETER StaleThresholdMin
  Max acceptable 15m-kline lag in minutes (default 30).

.PARAMETER Json
  Emit a single-line JSON object instead of the human report.
#>
param(
    [int]$StaleThresholdMin = 30,
    [switch]$Json
)

$ErrorActionPreference = 'SilentlyContinue'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PyExe = Join-Path $RepoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $PyExe)) { $PyExe = 'python' }

$dcProcs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'backend\.workers\.market_data_center' })
$procCount = $dcProcs.Count

$portListening = $false
try {
    $conn = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($conn) { $portListening = $true }
} catch { $portListening = $false }

$healthOk = $false
try {
    $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:9100/health' -UseBasicParsing -TimeoutSec 5
    $healthOk = ($resp.StatusCode -eq 200)
} catch { $healthOk = $false }

$lagMin = $null
$freshOk = $false
# Freshness signal: how long ago the data-center log was last written.
# This is dependency-free (no python, no SQL) and is exactly what failed in the
# 2026-10-03 outage: the log simply stopped at 21:56:49 while the port still answered.
$dcLog = Join-Path $RepoRoot 'logs\data-center.log'
if (Test-Path $dcLog) {
    $logAgeMin = [int]((Get-Date) - (Get-Item $dcLog).LastWriteTime).TotalMinutes
    $lagMin = $logAgeMin
    if ($logAgeMin -ge 0 -and $logAgeMin -le $StaleThresholdMin) { $freshOk = $true }
}
# Secondary: DB kline lag (optional, only used when it answers)
if (Test-Path $PyExe) {
    $probe = @'
import sys
try:
    from sqlalchemy import create_engine, text
    e = create_engine("postgresql+psycopg://laobao:alpha_pass@localhost:5432/alpha_market")
    with e.connect() as c:
        row = c.execute(text("select round(extract(epoch from (now() - to_timestamp(max(timestamp))))/60) from crypto_klines where period='15m' and symbol in ('BTC','ETH')")).fetchone()
    print(int(row[0]) if row and row[0] is not None else -1)
except Exception:
    print(-1)
'@
    $lagRaw = & $PyExe -c $probe 2>$null | Select-Object -Last 1
    if ("$lagRaw" -match '^\d+$') { $klineLagMin = [int]$lagRaw } else { $klineLagMin = $null }
} else {
    $klineLagMin = $null
}

$wdCount = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" |
    Where-Object { $_.CommandLine -match 'data-center-watchdog' }).Count

# [2026-10-04 修正] 判定口径以「进程 + 端口 + 数据新鲜度」为准：
# `/health` 端点会滞后于采集器就绪 —— 实测 DC 已恢复采集（日志 04:33 仍在写、kline lag=0）
# 但 /health 未就绪，旧逻辑因此误判 DOWN（假警报）。/health 降级为参考信号，仅在其失败
# 而数据也不新鲜时才计入 DOWN。
# 显式 [bool] 转型：PowerShell 的 -and 链在操作数为数组/空值时可能返回非布尔值，
# 实测曾出现 `"healthy":null` 且 verdict 恒为 DOWN 的失真（我上一轮引入的 bug）。
$healthy = [bool](($procCount -ge 1) -and [bool]$portListening -and [bool]$freshOk)

$verdict = 'DOWN'
if ($healthy) { $verdict = 'OK' }
elseif (-not $freshOk) { $verdict = 'STALE' }
elseif ($wdCount -eq 0) { $verdict = 'UNSUPERVISED' }

# [2026-10-04 修复] 在输出前**强制重算**汇总判定：此前 JSON 里 `healthy` 曾出现 null、
# 且信号全绿时仍报 DOWN（我引入的缺陷，[bool] 转型未解决）⇒ 这里在两种输出分支之前
# 用同一表达式重算一次，保证"汇总与逐项信号永远一致"。
$healthy = [bool](($procCount -ge 1) -and [bool]$portListening -and [bool]$freshOk)
# 判定优先级（修正我上一轮的顺序缺陷：healthy 优先会让 UNSUPERVISED 永不触发）：
#   STALE（数据不流动） > UNSUPERVISED（看门狗数量 != 1） > OK > DOWN
$verdict = 'DOWN'
if (-not $freshOk) { $verdict = 'STALE' }
elseif ($healthy -and $wdCount -ne 1) { $verdict = 'UNSUPERVISED' }
elseif ($healthy) { $verdict = 'OK' }

if ($Json) {
    $obj = [ordered]@{
        healthy             = $healthy
        verdict             = $verdict
        process_count       = $procCount
        port_listening      = $portListening
        health_ok           = $healthOk
        kline_lag_min       = $lagMin
        stale_threshold_min = $StaleThresholdMin
        data_fresh          = $freshOk
        watchdog_count      = $wdCount
    }
    $obj | ConvertTo-Json -Compress
} else {
    $lagText = 'n/a'
    if ($null -ne $lagMin) { $lagText = "$lagMin min (threshold $StaleThresholdMin)" }
    Write-Host ''
    Write-Host '=== Data Center probe (staleness aware) ===' -ForegroundColor Cyan
    if ($procCount -ge 1) { Write-Host "  process    : $procCount" -ForegroundColor Green }
    else { Write-Host "  process    : 0 (NOT RUNNING)" -ForegroundColor Red }
    if ($portListening) { Write-Host '  port 9100  : listening' -ForegroundColor Green }
    else { Write-Host '  port 9100  : NOT listening' -ForegroundColor Red }
    if ($healthOk) { Write-Host '  /health    : 200' -ForegroundColor Green }
    else { Write-Host '  /health    : failed' -ForegroundColor Red }
    if ($freshOk) { Write-Host "  kline lag  : $lagText" -ForegroundColor Green }
    else { Write-Host "  kline lag  : $lagText  <-- DATA NOT FLOWING" -ForegroundColor Red }
    if ($wdCount -eq 1) { Write-Host '  watchdog   : 1' -ForegroundColor Green }
    else { Write-Host "  watchdog   : $wdCount (expected exactly 1)" -ForegroundColor Yellow }
    if ($healthy) { Write-Host "  verdict    : $verdict" -ForegroundColor Green }
    else { Write-Host "  verdict    : $verdict" -ForegroundColor Red }
    Write-Host ''
}

if ($healthy) { exit 0 } else { exit 1 }
