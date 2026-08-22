@echo off
REM ============================================================
REM 一键重启（2026-08-22 22:40 版本）
REM 修复：venv 指向已删除目录 / vbs 路径 / 热重载开关 / 去掉后端看门狗
REM 用法：普通资源管理器双击即可（默认 NO_RELOAD 防子进程占口）
REM       若需热重载：先 set FORCE_RELOAD=1 再运行本 bat
REM ============================================================
cd /d %~dp0

echo [1/5] Stopping ALL project python processes (backend/DC/GUI)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -and ($_.Path -match '001Alpha') } | Stop-Process -Force -ErrorAction SilentlyContinue; Start-Sleep 5; Write-Host '  project python stopped'"

echo [2/5] Verifying Python runtime (pyvenv.cfg fix)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$cfg = Test-Path 'backend\.venv\pyvenv.cfg'; $rt = Test-Path '.runtime\Python312\python.exe'; if ($cfg -and $rt) { Write-Host '  runtime OK (project-internal, no D:\项目 dependency)' } else { Write-Host '  MISSING runtime! backend\.venv\pyvenv.cfg=' $cfg ' .runtime\Python312\python.exe=' $rt }"

echo [3/5] Starting Data Center (:9100)...
start "" /b powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-data-center.ps1

echo [4/5] Starting Backend (:8000)...
if "%FORCE_RELOAD%"=="1" (
  echo   Reload mode ON: 改 backend\**\*.py 自动重启（子进程残留由 stop-dev 清理）
  start "" /b cmd /c "set FORCE_RELOAD=1 && scripts\start-backend-fix.cmd"
) else (
  echo   Normal mode (NO_RELOAD): 改 .py 不会自动生效，需重启本 bat
  start "" /b cmd /c scripts\start-backend-fix.cmd
)

echo [5/5] Waiting for backend health (up to 8 min)...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ok=$false; for($i=0;$i -lt 96;$i++){ Start-Sleep 5; try { $r=Invoke-WebRequest 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 4; Write-Host ('BACKEND OK: ' + $r.Content); $ok=$true; break } catch {} }; if(-not $ok){ Write-Host 'TIMEOUT: backend not ready. Check logs:'; Get-Content logs\backend.log -Tail 5; Get-Content logs\backend.error.log -Tail 5 }"

echo.
echo ============================================================
echo 代码生效验证：运行
echo   python scripts\_verify_code_live.py
echo 对比 /api/health 的 boot_git_hash 与 git HEAD：
echo   boot_git_hash == 当前 HEAD  → 跑的就是最新代码
echo   boot_git_hash != 当前 HEAD  → 旧进程仍在，需要重启
echo ============================================================
pause
