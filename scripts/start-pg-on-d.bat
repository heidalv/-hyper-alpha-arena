@echo off
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 (
  echo Need admin
  pause
  exit /b 1
)

set "PG_BIN=C:\Program Files\PostgreSQL\15\bin"
set "PG_DATA_D=D:\PostgreSQL\15\data"
set "SVC=postgresql-x64-15"

taskkill /F /IM postgres.exe >nul 2>&1
timeout /t 2 /nobreak >nul
if exist "%PG_DATA_D%\postmaster.pid" del /f /q "%PG_DATA_D%\postmaster.pid"
if exist "C:\Program Files\PostgreSQL\15\data\postmaster.pid" del /f /q "C:\Program Files\PostgreSQL\15\data\postmaster.pid"

sc config "%SVC%" binPath= "\"%PG_BIN%\pg_ctl.exe\" runservice -N \"%SVC%\" -D \"%PG_DATA_D%\" -w"
net start "%SVC%"
if errorlevel 1 (
  echo service failed, pg_ctl start...
  "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" -l "%PG_DATA_D%\pg_start_d.log" start
)

timeout /t 5 /nobreak >nul
"%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432
if exist "%PG_DATA_D%\postmaster.pid" (
  echo PIDFILE:
  type "%PG_DATA_D%\postmaster.pid"
)
echo DONE > "D:\001Alpha\Hyper-Alpha-Arena\logs\pg-start-d-done.flag"
