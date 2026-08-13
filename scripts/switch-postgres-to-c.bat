@echo off
chcp 65001 >nul
title Switch PostgreSQL data dir back to C:\Program Files\PostgreSQL\15\data
echo.
echo 需要【管理员】权限：把服务数据目录改回 C 盘（当前可用库）。
echo.
net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] 请右键 → 以管理员身份运行
  pause
  exit /b 1
)

set "PG_BIN=C:\Program Files\PostgreSQL\15\bin"
set "PG_DATA_C=C:\Program Files\PostgreSQL\15\data"
set "SVC=postgresql-x64-15"

echo [1/3] 停止服务并结束 postgres...
net stop "%SVC%" >nul 2>&1
taskkill /F /IM postgres.exe >nul 2>&1
timeout /t 2 /nobreak >nul

echo [2/3] 配置服务到 C 盘...
sc config "%SVC%" binPath= "\"%PG_BIN%\pg_ctl.exe\" runservice -N \"%SVC%\" -D \"%PG_DATA_C%\" -w"

echo [3/3] 启动服务...
net start "%SVC%"
timeout /t 3 /nobreak >nul
netstat -ano | findstr ":5432" | findstr LISTENING
echo.
echo 完成。当前应使用: %PG_DATA_C%
pause
