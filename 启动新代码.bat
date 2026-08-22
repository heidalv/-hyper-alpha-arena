@echo off
REM ============================================================
REM 一键重启（强版）：加载 2026-08-22 修复后的代码与 .env
REM 在【普通资源管理器】里双击本文件即可
REM ============================================================
cd /d %~dp0

echo [1/4] Stopping ALL project python processes (backend/DC/GUI)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path -match '001Alpha') } | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep 5; $l8000 = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue; $l9100 = Get-NetTCPConnection -LocalPort 9100 -State Listen -ErrorAction SilentlyContinue; if ($l8000 -or $l9100) { Get-NetTCPConnection -LocalPort 8000,9100 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }; Start-Sleep 3; Write-Host '  ports freed' } else { Write-Host '  ports free' }"

echo [2/4] Starting Data Center (:9100)...
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-data-center.ps1

echo [3/4] Starting Backend (:8000)...
start "" /b cmd /c scripts\start-backend-fix.cmd

echo [4/4] Waiting for backend health (up to 6 min)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; for($i=0;$i -lt 72;$i++){ Start-Sleep 5; try { $r=Invoke-WebRequest 'http://127.0.0.1:8000/api/system/status' -UseBasicParsing -TimeoutSec 4; Write-Host ('BACKEND OK: ' + $r.Content); $ok=$true; break } catch {} }; if(-not $ok){ Write-Host 'TIMEOUT: backend not ready. Check:'; Get-Content logs\backend.log -Tail 5; Get-Content logs\backend.error.log -Tail 5 }"

echo.
echo 验证（可选）：python scripts\_verify_post_restart.py 5
pause
