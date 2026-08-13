@echo off
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 exit /b 1

set "PG_BIN=C:\Program Files\PostgreSQL\15\bin"
set "PG_DATA_D=D:\PostgreSQL\15\data"
set "AUTO=%PG_DATA_D%\postgresql.auto.conf"
set "FLAG=D:\001Alpha\Hyper-Alpha-Arena\logs\pg-d-ready.flag"
set "LOG=D:\001Alpha\Hyper-Alpha-Arena\logs\pg-d-fsync-once.log"

echo ==== %date% %time% > "%LOG%"

taskkill /F /IM postgres.exe >> "%LOG%" 2>&1
ping -n 4 127.0.0.1 >nul
if exist "%PG_DATA_D%\postmaster.pid" del /f /q "%PG_DATA_D%\postmaster.pid"
if exist "C:\Program Files\PostgreSQL\15\data\postmaster.pid" del /f /q "C:\Program Files\PostgreSQL\15\data\postmaster.pid"

echo # temporary migration boot %date% %time%> "%AUTO%.tmp"
echo fsync = off>> "%AUTO%.tmp"
echo synchronous_commit = off>> "%AUTO%.tmp"
echo full_page_writes = off>> "%AUTO%.tmp"
type "%AUTO%" >> "%AUTO%.tmp" 2>nul
move /y "%AUTO%.tmp" "%AUTO%" >> "%LOG%" 2>&1

sc config postgresql-x64-15 binPath= "\"%PG_BIN%\pg_ctl.exe\" runservice -N \"postgresql-x64-15\" -D \"%PG_DATA_D%\" -w" >> "%LOG%" 2>&1

echo starting pg_ctl -w -t 600 >> "%LOG%"
"%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" -l "%PG_DATA_D%\pg_start_d.log" -w -t 600 start >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo pg_ctl_exit=%RC% >> "%LOG%"

"%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432 >> "%LOG%" 2>&1
if errorlevel 1 (
  echo FAIL_ISREADY %date% %time%> "%FLAG%"
  type "%PG_DATA_D%\pg_start_d.log" >> "%LOG%"
  exit /b 1
)

echo READY_BOOT %date% %time%> "%FLAG%"
type "%PG_DATA_D%\postmaster.pid" >> "%FLAG%"

rem 恢复安全 fsync 并重启一次（此时已 clean，秒起）
echo.>> "%AUTO%"
echo # restore durable settings %date% %time%>> "%AUTO%"
echo fsync = on>> "%AUTO%"
echo synchronous_commit = on>> "%AUTO%"
echo full_page_writes = on>> "%AUTO%"

"%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" -m fast stop >> "%LOG%" 2>&1
ping -n 3 127.0.0.1 >nul
"%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" -l "%PG_DATA_D%\pg_start_d.log" -w -t 120 start >> "%LOG%" 2>&1
"%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432 >> "%LOG%" 2>&1
if errorlevel 1 (
  echo FAIL_AFTER_FSYNC_ON>> "%FLAG%"
  exit /b 2
)

echo READY_FINAL %date% %time%>> "%FLAG%"
type "%PG_DATA_D%\postmaster.pid" >> "%FLAG%"
net start postgresql-x64-15 >> "%LOG%" 2>&1
echo ALL_OK>> "%FLAG%"
exit /b 0
