@echo off
chcp 65001 >nul
cd /d "%~dp0.."
echo 启动 Heidalv-Alpha-Arena (Windows)...
echo.

if not exist "backend\.venv\Scripts\python.exe" (
    echo [错误] 未找到 Python 虚拟环境 backend\.venv，请先运行 scripts\setup-windows.ps1
    pause
    exit /b 1
)

echo 正在启动图形启动器 launcher.py ...
rem 用 venv 的 python（含 psycopg3 等依赖），避免误用系统 Python 导致后端起不来
"backend\.venv\Scripts\python.exe" launcher.py
