@echo off
chcp 65001 >nul
title 001Alpha Obsidian Launcher
setlocal enabledelayedexpansion

cd /d "%~dp0"
set "ROOT=%cd%"
set "VAULT=%ROOT%\obsidian_vault"
set "LOG_DIR=%ROOT%\logs"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" 2>nul

echo ==============================================
echo  001Alpha Obsidian Launcher (updated)
echo ==============================================
echo.
echo Root: %ROOT%
echo Vault: %VAULT%
echo.

echo [1/3] Starting Obsidian...
set "OBS_EXE="
if exist "%LOCALAPPDATA%\Obsidian\Obsidian.exe" set "OBS_EXE=%LOCALAPPDATA%\Obsidian\Obsidian.exe"
if exist "%LOCALAPPDATA%\Programs\Obsidian\Obsidian.exe" set "OBS_EXE=%LOCALAPPDATA%\Programs\Obsidian\Obsidian.exe"
if exist "C:\Program Files\Obsidian\Obsidian.exe" set "OBS_EXE=C:\Program Files\Obsidian\Obsidian.exe"
if exist "C:\Program Files (x86)\Obsidian\Obsidian.exe" set "OBS_EXE=C:\Program Files (x86)\Obsidian\Obsidian.exe"

if defined OBS_EXE (
    echo   Found: %OBS_EXE%
    start "" "%OBS_EXE%" "%VAULT%"
    echo   Done.
) else (
    start "" "obsidian://open?vault=obsidian_vault"
    echo   Sent obsidian:// request.
    echo   If Obsidian does not open, download from:
    echo   https://obsidian.md/download
)
echo.

echo [2/3] Checking plugins...
if exist "%VAULT%\.obsidian\plugins\dataview\main.js" (
    echo   Dataview: installed
) else (
    echo   Dataview: not installed
    echo   Run: powershell scripts\setup_obsidian_plugins.ps1
)
if exist "%VAULT%\.obsidian\plugins\obsidian-local-rest-api\main.js" (
    echo   REST API: installed
) else (
    echo   REST API: not installed (optional)
)
echo.

echo [3/3] Starting bridge (if backend is running)...
curl -s -o nul -w "%%{http_code}" http://localhost:8000/health 2>nul | find "200" >nul
if !errorlevel! equ 0 (
    tasklist /FI "WINDOWTITLE eq 001Alpha-Bridge" 2>nul | find "python" >nul
    if !errorlevel! neq 0 (
        start "001Alpha-Bridge" cmd /c "cd /d "%ROOT%" && python backend\services\obsidian_bridge.py >>"%LOG_DIR%\bridge.log" 2>>&1"
        echo   Bridge started.
    ) else (
        echo   Bridge already running.
    )
) else (
    echo   Backend not running, bridge skipped.
    echo   For full stack: double-click scripts\start_all.bat
)
echo.

echo ==============================================
echo  Done.
echo  1. Open Obsidian -> Settings -> Community plugins
echo  2. Enable Dataview (already installed)
echo  3. Dashboard tables will appear
echo ==============================================
echo.
pause