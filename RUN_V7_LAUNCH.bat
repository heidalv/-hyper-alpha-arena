@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
set V7_LOG=logs\v7_launch.log
echo [%date% %time%] V7 launch start >> "%V7_LOG%"

echo ============================================================
echo   V7 自进化因子工厂 - 正式上线
echo ============================================================

if not exist "backend\.venv\Scripts\python.exe" (
    echo [ERROR] backend\.venv not found. Run QUICK.bat setup first.
    pause
    exit /b 1
)

echo [1/5] V7 接线自检...
call RUN_V7_CHECK.bat /nopause
if errorlevel 1 (
    echo [%date% %time%] check failed >> "%V7_LOG%"
    echo [ABORT] V7 检查未通过，停止上线
    pause
    exit /b 1
)
echo [%date% %time%] check ok >> "%V7_LOG%"

echo [2/5] 停止旧后端（端口 8000）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /PID %%p /T /F >nul 2>&1
)
timeout /t 2 /nobreak >nul

echo [3/5] 启动后端（注册 V7 每日 4h/5m/15m 进化 + 记忆维护调度）...
start "AlphaArena-Backend-V7" /D "%~dp0" cmd /k "backend\.venv\Scripts\python.exe backend\start_server.py"

echo [4/5] 等待后端健康...
set /a tries=0
:waitloop
timeout /t 3 /nobreak >nul
set /a tries+=1
powershell -NoProfile -Command "try { $r=Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing -TimeoutSec 3; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto healthy
if %tries% LSS 30 goto waitloop
echo [ABORT] 30 次未等到后端健康，请检查 logs\backend.log
pause
exit /b 1
:healthy
echo [OK] 后端健康检查通过

if "%V7_LAUNCH_FULL_NOW%"=="1" (
    echo [5/5] 按 V7_LAUNCH_FULL_NOW=1 立即启动三周期完整进化（新窗口后台运行）...
    start "V7-Factor-Evolution-Full" /D "%~dp0" cmd /k "backend\.venv\Scripts\python.exe -m backend.services.evolution.evolution_v7_runner run --periods 4h,15m,5m"
) else (
    echo [5/5] 无需人工执行：
    echo       后端已自动注册每日 03:00 4h / 04:00 5m / 06:00 15m / 06:50 记忆维护
    echo       启动后 45 秒会自动补一轮 4h quick 进化（若 6 小时内未跑过）
)

echo.
echo ============================================================
echo   V7 正式上线完成
echo   - 长期记忆: backend\data\factor_evolution_memory_v7.db
echo   - 状态API:  http://127.0.0.1:8000/api/evolution/v7-memory
echo   - 手动维护: http://127.0.0.1:8000/api/evolution/v7-memory/maintenance
echo ============================================================
pause
endlocal
