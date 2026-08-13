@echo off
cd /d D:\001Alpha\Hyper-Alpha-Arena
set DATA_CENTER_MODE=standalone
set BACKEND_PORT=8000
set BACKEND_HOST=0.0.0.0
set NO_RELOAD=true
if not exist logs mkdir logs
".venv\Scripts\python.exe" "scripts\run_uvicorn_dev.py" >> logs\backend.log 2>&1
