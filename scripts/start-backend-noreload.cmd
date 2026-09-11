@echo off
REM ASCII-only NO_RELOAD launcher for scheduled-task use (2026-08-28)
cd /d D:\001Alpha\Hyper-Alpha-Arena
set NO_RELOAD=true
set DATA_CENTER_MODE=standalone
set BACKEND_PORT=8000
set BACKEND_HOST=0.0.0.0
".venv\Scripts\python.exe" "scripts\run_uvicorn_dev.py" >> logs\backend.log 2>&1
