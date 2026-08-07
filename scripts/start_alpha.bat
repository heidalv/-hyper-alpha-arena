@echo off
chcp 65001 >nul
title 001Alpha - Launcher
setlocal enabledelayedexpansion

set "ROOT=D:\BaiduNetdiskDownload\001Alpha-Windows迁移包-20260610-2228\001Alpha\Hyper-Alpha-Arena"
set "VAULT=%ROOT%\obsidian_vault"
set "LOG_DIR=%ROOT%\logs"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" 2>nul

echo ==============================================
echo  001Alpha Launcher
echo ==============================================
echo.
echo Root: %ROOT%
echo.

echo [1/4] Checking backend...
curl -s -o nul -w "%%%%{http_code}" http://localhost:8000/health 2>nul | find "200" >nul
if !errorlevel! equ 0 (
    echo   Backend already running.
) else (
    echo   Starting backend...
    start "001Alpha-Backend" cmd /c "cd /d "%ROOT%" && python backend\start_server.py 2>>"%LOG_DIR%\backend_err.log" >>"%LOG_DIR%\backend.log""
    echo   Backend started.
)
echo.

echo [2/4] Starting Obsidian Bridge...
tasklist /FI "WINDOWTITLE eq 001Alpha-Bridge" 2>nul | find "python" >nul
if !errorlevel! equ 0 (
    echo   Bridge already running.
) else (
    start "001Alpha-Bridge" cmd /c "cd /d "%ROOT%" && python backend\services\obsidian_bridge.py 2>>"%LOG_DIR%\bridge_err.log" >>"%LOG_DIR%\bridge.log""
    echo   Bridge started.
)
echo.

echo [3/4] Opening Obsidian vault...
start "" "obsidian://open?vault=obsidian_vault"
echo.

echo [4/4] Checking plugins...
if exist "%VAULT%\.obsidian\plugins\dataview\main.js" (
    echo   Dataview: installed
) else (
    echo   Dataview: not installed - run scripts\setup_obsidian_plugins.ps1
)
echo.

echo ==============================================
echo  Done. Enable Dataview in Obsidian to see tables.
echo ==============================================
echo.
pause