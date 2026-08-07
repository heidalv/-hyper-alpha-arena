@echo off
chcp 65001 >nul
cd /d D:\BaiduNetdiskDownload\001Alpha\001Alpha\Hyper-Alpha-Arena
echo ==============================================
echo  001Alpha Launcher
echo ==============================================
echo.
echo Root: %cd%
echo.
echo [1/4] Starting backend...
start "001Alpha-Backend" python backend\start_server.py
echo [2/4] Starting Bridge...
start "001Alpha-Bridge" python backend\services\obsidian_bridge.py
echo [3/4] Opening Obsidian...
start obsidian://open?vault=obsidian_vault
echo [4/4] Dataview exists: YES
echo.
echo ==============================================
echo  Done! Enable Dataview in Obsidian to see tables.
echo ==============================================
pause
