@echo off
set BACKEND_PORT=8000
set BACKEND_HOST=0.0.0.0
set NO_RELOAD=true
set DATA_CENTER_MODE=standalone
set PYTHONUNBUFFERED=1
start "" /b "D:\001Alpha\Hyper-Alpha-Arena\backend\.venv\Scripts\python.exe" "D:\001Alpha\Hyper-Alpha-Arena\scripts\run_uvicorn_dev.py" >> "D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log" 2>> "D:\001Alpha\Hyper-Alpha-Arena\logs\backend.error.log"
