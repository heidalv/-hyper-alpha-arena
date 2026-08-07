# 独立数据中心采集进程（不随主 API 重启而停）
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$env:DATA_CENTER_MODE = "standalone"
$env:DATA_CENTER_PROCESS = "1"
$py = if (Test-Path "backend\.venv\Scripts\python.exe") {
  "backend\.venv\Scripts\python.exe"
} elseif (Test-Path ".venv\Scripts\python.exe") {
  ".venv\Scripts\python.exe"
} else { "python" }
Write-Host "[DataCenter] $py -m backend.workers.market_data_center"
Write-Host "[DataCenter] health http://127.0.0.1:9100/health"
& $py -m backend.workers.market_data_center
