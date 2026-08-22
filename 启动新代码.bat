@echo off
REM ============================================================
REM 一键重启（自愈强版）2026-08-22 22:00
REM 修复了导致多次启动失败的根因：
REM   1) backend\.venv\pyvenv.cfg 指向已删除的 D:\项目\001Alpha\.runtime
REM      → 已改为指向 D:\001Alpha\Hyper-Alpha-Arena\.runtime\Python312
REM   2) 项目 .runtime\Python312 解释器已复制（不再依赖旧目录）
REM   3) 启动管理器.vbs 的 venv 路径 bug 已修（backend\venv → backend\.venv）
REM   4) 本 bat 增加后端自愈看门狗（8000 挂了自动重启）
REM 在【普通资源管理器】里双击本文件即可
REM ============================================================
cd /d %~dp0

echo [1/5] Stopping ALL project python processes (backend/DC/GUI)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path -match '001Alpha') } | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep 5; Write-Host '  project python stopped'"

echo [2/5] Verifying Python runtime (pyvenv.cfg fix)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$cfg = Test-Path 'backend\.venv\pyvenv.cfg'; $rt = Test-Path '.runtime\Python312\python.exe'; if ($cfg -and $rt) { Write-Host '  runtime OK (project-internal)' } else { Write-Host '  MISSING runtime!'; Write-Host '  backend\.venv\pyvenv.cfg=' $cfg ' .runtime\Python312\python.exe=' $rt }"

echo [3/5] Starting Data Center (:9100)...
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-data-center.ps1

echo [4/5] Starting Backend (:8000) + self-healing watchdog...
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-backend-fix.cmd
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -File scripts\backend-watchdog.ps1

echo [5/5] Waiting for backend health (up to 8 min)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; for($i=0;$i -lt 96;$i++){ Start-Sleep 5; try { $r=Invoke-WebRequest 'http://127.0.0.1:8000/api/system/status' -UseBasicParsing -TimeoutSec 4; Write-Host ('BACKEND OK: ' + $r.Content.Substring(0,[Math]::Min(120,$r.Content.Length))); $ok=$true; break } catch {} }; if(-not $ok){ Write-Host 'TIMEOUT: backend not ready. Check logs:'; Get-Content logs\backend.log -Tail 5; Get-Content logs\backend.error.log -Tail 5 }"

echo.
echo 验证（可选）：python scripts\_verify_post_restart.py 5
echo 若 BACKEND OK 显示成功，后端已由 watchdog 守护，之后自动自愈。
pause
