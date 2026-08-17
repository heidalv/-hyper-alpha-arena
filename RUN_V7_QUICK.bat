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

echo [V7] 快速验证闭环：4h + 15m + 5m（仅种子/模板，跳过 GP/MCTS，先跑通再放开）
backend\.venv\Scripts\python.exe -m backend.services.evolution.evolution_v7_runner run --periods 4h,15m,5m --quick
if errorlevel 1 (
    echo [FAIL] V7 quick run 失败，见上方输出
    pause
    exit /b 1
)
echo [OK] V7 quick 闭环完成，长期记忆已写入 backend\data\factor_evolution_memory_v7.db
pause
endlocal
