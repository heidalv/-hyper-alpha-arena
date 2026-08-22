@echo off
REM ============================================================
REM 一键重启：加载 2026-08-22 修复后的代码与 .env（含 M0/M1/M2/M3）
REM 在【普通资源管理器】里双击本文件即可（管理员身份无需）
REM ============================================================
cd /d %~dp0

echo [1/4] Stopping old backend / DC / stray launcher processes...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-NetTCPConnection -LocalPort 8000,9100 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'Hyper-Alpha-Arena' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep 3"
if errorlevel 1 echo   [WARN] some processes may not have been freed (check Task Manager)

echo [2/4] Ensuring Data Center (:9100)...
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-data-center.ps1

echo [3/4] Starting Backend (:8000)...
start "" /b cmd /c scripts\start-backend-fix.cmd

echo [4/4] Waiting for backend health (up to 5 min)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; for($i=0;$i -lt 60;$i++){ Start-Sleep 5; try { $r=Invoke-WebRequest 'http://127.0.0.1:8000/api/system/status' -UseBasicParsing -TimeoutSec 4; Write-Host ('BACKEND OK: ' + $r.Content); $ok=$true; break } catch {} }; if(-not $ok){ Write-Host 'TIMEOUT: backend not ready - check logs\backend.log / logs\backend.error.log' }"

echo.
echo Done. Then run:  python scripts\_verify_post_restart.py 5
pause
