@echo off
cd /d D:\001Alpha\Hyper-Alpha-Arena
set DATA_CENTER_MODE=standalone
set BACKEND_PORT=8000
set BACKEND_HOST=0.0.0.0
REM 常驻/生产：关热重载。若开 reload，勿把 stdout 重定向到 backend.log（Windows 会锁文件）。
set NO_RELOAD=true
if not exist logs mkdir logs
".venv\Scripts\python.exe" "scripts\run_uvicorn_dev.py" >> logs\uvicorn-stdout.log 2>&1
