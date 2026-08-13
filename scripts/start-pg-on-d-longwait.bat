@echo off
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 exit /b 1

set "PG_BIN=C:\Program Files\PostgreSQL\15\bin"
set "PG_DATA_D=D:\PostgreSQL\15\data"
set "FLAG=D:\001Alpha\Hyper-Alpha-Arena\logs\pg-d-ready.flag"
set "LOG=D:\001Alpha\Hyper-Alpha-Arena\logs\pg-d-longwait.log"

echo start %date% %time% > "%LOG%"
taskkill /F /IM postgres.exe >> "%LOG%" 2>&1
timeout /t 3 /nobreak >nul
if exist "%PG_DATA_D%\postmaster.pid" del /f /q "%PG_DATA_D%\postmaster.pid"
if exist "C:\Program Files\PostgreSQL\15\data\postmaster.pid" del /f /q "C:\Program Files\PostgreSQL\15\data\postmaster.pid"

rem 服务启动超时太短会杀掉正在 fsync 的进程；先用 pg_ctl 无等待启动
sc config postgresql-x64-15 binPath= "\"%PG_BIN%\pg_ctl.exe\" runservice -N \"postgresql-x64-15\" -D \"%PG_DATA_D%\" -w" >> "%LOG%" 2>&1

echo launching pg_ctl at %time% >> "%LOG%"
start "postgres-d" /MIN "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" -l "%PG_DATA_D%\pg_start_d.log" start
echo pg_ctl issued >> "%LOG%"

rem 最长等 15 分钟（49GB 首次 fsync 很慢）
set OK=0
for /L %%i in (1,1,180) do (
  "%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432 >nul 2>&1
  if not errorlevel 1 (
    if exist "%PG_DATA_D%\postmaster.pid" (
      set OK=1
      goto done
    )
  )
  echo wait %%i %time% >> "%LOG%"
  timeout /t 5 /nobreak >nul
)

:done
if "%OK%"=="1" (
  echo READY %date% %time% > "%FLAG%"
  type "%PG_DATA_D%\postmaster.pid" >> "%FLAG%"
  echo READY >> "%LOG%"
  rem 数据库已起来后，尝试把服务状态对齐（此时 start 会很快）
  net start postgresql-x64-15 >> "%LOG%" 2>&1
) else (
  echo FAIL %date% %time% > "%FLAG%"
  echo FAIL >> "%LOG%"
)
