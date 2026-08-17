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

echo [V7] 完整三周期因子进化：4h + 15m + 5m（GP + MCTS + Codegen LLM + WFO/DSR/PBO）
echo      每周期有硬时间预算（FACTOR_EVO_BUDGET_MAX_SEC，默认1800秒）。
backend\.venv\Scripts\python.exe -m backend.services.evolution.evolution_v7_runner run --periods 4h,15m,5m
if errorlevel 1 (
    echo [FAIL] V7 full run 失败，见上方输出
    pause
    exit /b 1
)
echo [OK] V7 完整闭环完成，记忆已反哺 Codegen prompt。
pause
endlocal
