@echo off
chcp 65001 >nul
echo ============================================================
echo  Python 3.12 升级脚本
echo ============================================================
echo.

cd /d "%~dp0"

echo [步骤 1/5] 停止当前服务...
if exist "STOP.bat" (
    call STOP.bat
) else (
    echo   跳过STOP.bat
)
timeout /t 3 /nobreak >nul
echo [OK] 服务已停止
echo.

echo [步骤 2/5] 备份旧虚拟环境...
if exist "backend\.venv" (
    if exist "backend\.venv.old" (
        rmdir /s /q "backend\.venv.old"
    )
    ren "backend\.venv" ".venv.old"
    echo [OK] 已备份到 backend\.venv.old
) else (
    echo [INFO] 无需备份
)
echo.

echo [步骤 3/5] 创建Python 3.12虚拟环境...
python -m venv backend\.venv
if errorlevel 1 (
    echo [ERROR] 虚拟环境创建失败!
    pause
    exit /b 1
)
echo [OK] 新虚拟环境创建完成
echo.

echo [步骤 4/5] 验证Python版本...
backend\.venv\Scripts\python.exe --version
echo.

echo [步骤 5/5] 安装依赖包...
echo   这可能需要几分钟时间,请耐心等待...
echo.
backend\.venv\Scripts\python.exe -m pip install --upgrade pip
backend\.venv\Scripts\pip.exe install -r backend\requirements.txt
if errorlevel 1 (
    echo [ERROR] 依赖安装失败!
    echo   请检查网络连接或手动执行:
    echo   backend\.venv\Scripts\pip.exe install -r backend\requirements.txt
    pause
    exit /b 1
)
echo [OK] 依赖安装完成
echo.

echo ============================================================
echo  升级完成!
echo ============================================================
echo.
echo 下一步:
echo   1. 测试pandas_ta是否可用:
echo      backend\.venv\Scripts\python.exe -c "import pandas_ta; print('pandas_ta OK')"
echo.
echo   2. 重新启动系统:
echo      QUICK.bat
echo.
pause
