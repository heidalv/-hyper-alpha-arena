@echo off
setlocal
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist "backend\.venv\Scripts\python.exe" (
    echo [ERROR] backend\.venv not found. Run QUICK.bat setup first.
    pause
    exit /b 1
)

echo [V7] Python 语法检查（main/evolution_routes/factor_evolution_loop/memory/runner）...
backend\.venv\Scripts\python.exe -m py_compile backend\main.py backend\api\evolution_routes.py backend\services\evolution\factor_evolution_loop.py backend\services\evolution\evolution_memory_v7.py backend\services\evolution\evolution_v7_runner.py
if errorlevel 1 (
    echo [FAIL] Python 语法检查失败
    pause
    exit /b 1
)

echo [V7] 检查长期记忆与因子进化接线...
backend\.venv\Scripts\python.exe -m backend.services.evolution.evolution_v7_runner check
if errorlevel 1 (
    echo [FAIL] V7 检查未通过，见上方输出
    pause
    exit /b 1
)
echo [OK] V7 就绪。
if /i not "%1"=="/nopause" pause
endlocal
