@echo off
rem ============================================================
rem  Heidalv-Alpha-Arena - One-click start (Windows)
rem  Backend : port 8000  (FastAPI / uvicorn)
rem  Frontend: port 5273  (frontend-next)  —— 旧 Vite :5173 已冻结
rem ============================================================
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
    echo [ERROR] Backend venv not found. Run in PowerShell:
    echo     python -m uv sync --directory backend
    pause
    exit /b 1
)

if not exist "frontend-next\node_modules" (
    echo [ERROR] frontend-next deps not found. Run:
    echo     cd frontend-next ^&^& npm install
    pause
    exit /b 1
)

echo [1/3] Starting backend on port 8000 ...
start "AlphaArena-Backend" cmd /k "cd /d "%~dp0" && set DATA_CENTER_MODE=standalone&& backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"

echo [2/3] Starting frontend-next on port 5273 ...
start "AlphaArena-Frontend" cmd /k "cd /d "%~dp0frontend-next" && npm run dev"

echo [3/3] Waiting 10 seconds, then opening browser ...
timeout /t 10 /nobreak >nul
start http://127.0.0.1:5273/login

echo.
echo Done. Backend :8000 + frontend-next :5273
echo (旧 Vite :5173 已冻结，勿再使用)
echo To stop everything, run STOP.bat
echo 推荐日常用 DESKTOP.bat 或 dev-start.bat
pause
