@echo off
chcp 65001 >nul
title Heidalv Alpha Arena Desktop
cd /d "%~dp0"

echo.
echo  === Heidalv Alpha Arena 桌面端 ===
echo  后端 :8000  +  前端 Electron(登录页)
echo.

REM 若后端未监听 8000，尝试启动
powershell -NoProfile -Command "if (-not (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue)) { Write-Host '[start] backend...'; Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','backend.main:app','--host','0.0.0.0','--port','8000' -WorkingDirectory '%cd%' -WindowStyle Minimized }"

cd frontend-next
if not exist node_modules (
  echo [install] frontend-next dependencies...
  call npm install
)

echo [start] Electron desktop...
REM 若 5273 已有 Next，只开 Electron；否则 electron:dev:full 同时起 Next
powershell -NoProfile -Command "if (Get-NetTCPConnection -LocalPort 5273 -State Listen -ErrorAction SilentlyContinue) { npm run electron:dev } else { npm run electron:dev:full }"
