@echo off
chcp 65001 >nul
title Switch PostgreSQL data dir to D:\PostgreSQL\15\data
echo.
echo ============================================================
echo  把 PostgreSQL 切到 D 盘（需管理员）
echo  警告：上次直接切换时 D 盘库启动崩溃(0xC0000409)。
echo  正确做法：
echo    1) 先在 C 盘库运行时做一次干净停库
echo    2) 再把 C 数据同步/复制到 D（或确认 D 是完整副本）
echo    3) 最后再执行本脚本
echo  若只想立刻恢复可用，请改用 switch-postgres-to-c.bat
echo ============================================================
echo.
net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] 请右键本文件 → 以管理员身份运行
  pause
  exit /b 1
)

set "PG_BIN=C:\Program Files\PostgreSQL\15\bin"
set "PG_DATA_D=D:\PostgreSQL\15\data"
set "SVC=postgresql-x64-15"

if not exist "%PG_DATA_D%\PG_VERSION" (
  echo [ERROR] 找不到 D 盘数据目录: %PG_DATA_D%
  pause
  exit /b 1
)

echo [1/4] 干净停止...
net stop "%SVC%" >nul 2>&1
"%PG_BIN%\pg_ctl.exe" -D "C:\Program Files\PostgreSQL\15\data" stop -m fast >nul 2>&1
"%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" stop -m fast >nul 2>&1
taskkill /F /IM postgres.exe >nul 2>&1
timeout /t 3 /nobreak >nul

echo [2/4] 改服务到 D 盘...
sc config "%SVC%" binPath= "\"%PG_BIN%\pg_ctl.exe\" runservice -N \"%SVC%\" -D \"%PG_DATA_D%\" -w"
if errorlevel 1 (
  echo [ERROR] sc config 失败
  pause
  exit /b 1
)

echo [3/4] 启动服务...
net start "%SVC%"
if errorlevel 1 (
  echo [WARN] 服务启动失败，尝试 pg_ctl...
  "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" -l "%PG_DATA_D%\pg_switch.log" start
)

echo [4/4] 检查...
timeout /t 5 /nobreak >nul
if exist "%PG_DATA_D%\postmaster.pid" (
  echo [OK] D 盘 postmaster.pid 已生成
  type "%PG_DATA_D%\postmaster.pid"
) else (
  echo [FAIL] D 盘未成功启动，请查看 %PG_DATA_D%\log
  echo 建议立刻运行 switch-postgres-to-c.bat 恢复
)
netstat -ano | findstr ":5432" | findstr LISTENING
echo.
pause
