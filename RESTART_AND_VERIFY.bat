@echo off
rem ============================================================
rem  多交易所架构重构 - 重启验证脚本
rem  用途: 重启后端并自动运行验证测试
rem ============================================================
cd /d "%~dp0"

echo.
echo ========================================
echo   多交易所市场流采集架构 - 重启验证
echo ========================================
echo.

echo [步骤 1/4] 停止当前后端服务...
if exist "STOP.bat" (
    call STOP.bat
) else (
    echo [WARN] 未找到 STOP.bat,请手动停止后端进程
)
timeout /t 3 /nobreak >nul

echo.
echo [步骤 2/4] 检查配置...
echo ACTIVE_MARKET_FLOW_EXCHANGES=%ACTIVE_MARKET_FLOW_EXCHANGES%
echo DEFAULT_EXCHANGE=%DEFAULT_EXCHANGE%
echo.
echo [INFO] 默认配置 (settings.py):
echo   ACTIVE_MARKET_FLOW_EXCHANGES = hyperliquid,asterdex
echo   DEFAULT_EXCHANGE = asterdex
echo   CVD_AGGREGATION_WINDOW_SECONDS = 15
echo.

echo [步骤 3/4] 启动后端服务...
if exist "QUICK.bat" (
    start "AlphaArena-Backend-Restart" cmd /k "cd /d "%~dp0" && QUICK.bat"
    echo [OK] 已启动 QUICK.bat (包含前端+后端)
) else if exist "backend\.venv\Scripts\python.exe" (
    start "AlphaArena-Backend" cmd /k "cd /d "%~dp0" && backend\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
    echo [OK] 已启动后端服务 (端口 8000)
) else (
    echo [ERROR] 未找到启动脚本或虚拟环境
    pause
    exit /b 1
)

echo.
echo [步骤 4/4] 等待后端启动 (30秒)...
timeout /t 30 /nobreak

echo.
echo ========================================
echo   运行验证测试
echo ========================================
echo.

if exist "backend\.venv\Scripts\python.exe" (
    backend\.venv\Scripts\python.exe backend/verify_multi_exchange.py
) else (
    echo [ERROR] 未找到 Python 虚拟环境
    pause
    exit /b 1
)

echo.
echo ========================================
echo   验证完成!
echo ========================================
echo.
echo [下一步操作]:
echo   1. 查看上方测试结果
echo   2. 如果显示 "[WARN] 仅检测到 Hyperliquid 数据",请:
echo      - 等待 2-3 分钟让 Aster DEX 采集器开始工作
echo      - 再次运行: python backend/verify_multi_exchange.py
echo   3. 查看详细日志: logs/backend.log
echo   4. 查看完整测试报告: MULTI_EXCHANGE_TEST_REPORT.md
echo.
pause
