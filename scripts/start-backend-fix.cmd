@echo off
REM 后端启动器（NO_RELOAD 默认；FORCE_RELOAD=1 显式开启热重载）
REM [2026-08-22] 热重载默认关闭——uvicorn --reload 子进程在 Windows 上残留
REM 占 8000 导致"新进程起不来/卡死"（见 start-dev.ps1 顶部注释）。
REM 开发需要热重载时：set FORCE_RELOAD=1 后运行本脚本（或 dev-start.bat）。
cd /d D:\001Alpha\Hyper-Alpha-Arena
set DATA_CENTER_MODE=standalone
set BACKEND_PORT=8000
set BACKEND_HOST=0.0.0.0
if "%FORCE_RELOAD%"=="1" (
  set NO_RELOAD=
) else (
  set NO_RELOAD=true
)
if not exist logs mkdir logs
".venv\Scripts\python.exe" "scripts\run_uvicorn_dev.py" >> logs\backend.log 2>&1
