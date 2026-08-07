@echo off
chcp 65001 > nul
title Hyper-Alpha Data Center (standalone)
cd /d "%~dp0.."
if not exist "logs" mkdir logs
echo [DataCenter] starting standalone collector...
echo [DataCenter] health: http://127.0.0.1:9100/health
echo [DataCenter] log: logs\data-center.log
echo.
set DATA_CENTER_MODE=standalone
set DATA_CENTER_PROCESS=1
if exist "backend\.venv\Scripts\python.exe" (
  "backend\.venv\Scripts\python.exe" -m backend.workers.market_data_center
) else if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m backend.workers.market_data_center
) else (
  python -m backend.workers.market_data_center
)
echo.
echo [DataCenter] exited. 按任意键关闭…
pause > nul
