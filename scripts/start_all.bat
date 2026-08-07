@echo off
chcp 65001 >nul
title 001Alpha - Full Stack Start
setlocal enabledelayedexpansion

cd /d "%~dp0.."
set "ROOT=%cd%"
set "VAULT=%ROOT%\obsidian_vault"
set "LOG_DIR=%ROOT%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo ==============================================
echo  001Alpha Full Stack Launcher
echo ==============================================
echo.
echo Root: %ROOT%
echo Vault: %VAULT%
echo.

:check_backend
echo [1/4] Checking backend status...
curl -s -o nul -w "%%{http_code}" http://localhost:8000/health 2>nul | find "200" >nul
if !errorlevel! equ 0 (
    echo   Backend already running (port 8000)
    set "BACKEND_STARTED=no"
) else (
    echo   Starting backend...
    start "001Alpha-Backend" cmd /c "cd /d "%ROOT%" && python backend\start_server.py 2>>"%LOG_DIR%\backend_err.log" >>"%LOG_DIR%\backend.log""
    echo   Backend started (port 8000)
    echo   Log: %LOG_DIR%\backend.log
    set "BACKEND_STARTED=yes"
)
echo.

:start_bridge
echo [2/4] Starting Obsidian Bridge sync service...
tasklist /FI "WINDOWTITLE eq 001Alpha-Bridge" 2>nul | find "python" >nul
if !errorlevel! equ 0 (
    echo   Bridge already running.
) else (
    start "001Alpha-Bridge" cmd /c "cd /d "%ROOT%" && python backend\services\obsidian_bridge.py 2>>"%LOG_DIR%\bridge_err.log" >>"%LOG_DIR%\bridge.log""
    echo   Bridge started (30s sync interval)
    echo   Log: %LOG_DIR%\bridge.log
)
echo.

:start_obsidian
echo [3/4] Opening Obsidian vault...
start "" "obsidian://open?vault=obsidian_vault"
echo   obsidian:// request sent.
echo.

:plugin_check
echo [4/4] Checking Obsidian plugins...
if exist "%VAULT%\.obsidian\plugins\dataview\main.js" (
    if exist "%VAULT%\.obsidian\plugins\dataview\manifest.json" (
        echo   Dataview: installed
    )
)
if exist "%VAULT%\.obsidian\plugins\obsidian-local-rest-api\main.js" (
    echo   REST API: installed
) else (
    echo   REST API: not installed (optional, see setup script)
)

if not exist "%VAULT%\.obsidian\plugins\dataview\main.js" (
    echo.
    echo   Dataview not found. Run plugin installer:
    echo   PowerShell scripts\setup_obsidian_plugins.ps1
)
echo.

:done
echo ==============================================
echo  Launch complete!
echo.
echo  If Obsidian doesn't open automatically:
echo    1. Open Obsidian -> Open local folder
echo    2. Select: %VAULT%
echo.
echo  Stop: scripts\stop_all.bat
echo  Logs: %LOG_DIR%
echo ==============================================
echo.
pause