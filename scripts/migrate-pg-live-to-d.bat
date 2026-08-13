@echo off
chcp 65001 >nul
title Migrate PostgreSQL live data to D: and run ONLY from D:
echo.
echo ============================================================
echo  目标：只使用 D:\PostgreSQL\15\data （不再用 C 盘库）
echo  步骤：停库 → 把 C 最新数据同步到 D → 服务改指 D → 启动
echo  需要【管理员】权限
echo ============================================================
echo.
net session >nul 2>&1
if errorlevel 1 (
  echo [ERROR] 请右键本文件 → 以管理员身份运行
  pause
  exit /b 1
)

set "PG_BIN=C:\Program Files\PostgreSQL\15\bin"
set "PG_DATA_C=C:\Program Files\PostgreSQL\15\data"
set "PG_DATA_D=D:\PostgreSQL\15\data"
set "SVC=postgresql-x64-15"
set "LOG=D:\001Alpha\Hyper-Alpha-Arena\logs\pg-migrate-to-d.log"

if not exist "D:\001Alpha\Hyper-Alpha-Arena\logs" mkdir "D:\001Alpha\Hyper-Alpha-Arena\logs"
echo ==== %date% %time% ==== > "%LOG%"

echo [1/6] 停止所有 PostgreSQL...
net stop "%SVC%" >> "%LOG%" 2>&1
"%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_C%" stop -m fast >> "%LOG%" 2>&1
"%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" stop -m fast >> "%LOG%" 2>&1
taskkill /F /IM postgres.exe >> "%LOG%" 2>&1
timeout /t 3 /nobreak >nul

if exist "%PG_DATA_C%\postmaster.pid" del /f /q "%PG_DATA_C%\postmaster.pid"
if exist "%PG_DATA_D%\postmaster.pid" del /f /q "%PG_DATA_D%\postmaster.pid"

echo [2/6] 同步 C -^> D （可能要十几分钟，请勿关闭）...
echo 同步开始 %time% >> "%LOG%"
robocopy "%PG_DATA_C%" "%PG_DATA_D%" /MIR /R:2 /W:2 /NFL /NDL /NP /XD pg_wal\archive_status >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo robocopy exit=%RC% >> "%LOG%"
rem robocopy 0-7 = success-ish
if %RC% GEQ 8 (
  echo [ERROR] robocopy 失败，exit=%RC%，见 %LOG%
  pause
  exit /b 1
)

echo [3/6] 授权 NetworkService 访问 D 盘数据目录...
icacls "%PG_DATA_D%" /grant "NT AUTHORITY\NetworkService:(OI)(CI)F" /T /C >> "%LOG%" 2>&1
icacls "D:\PostgreSQL" /grant "NT AUTHORITY\NetworkService:(OI)(CI)F" /T /C >> "%LOG%" 2>&1

echo [4/6] 服务改指向 D 盘...
sc config "%SVC%" binPath= "\"%PG_BIN%\pg_ctl.exe\" runservice -N \"%SVC%\" -D \"%PG_DATA_D%\" -w" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [ERROR] sc config 失败
  pause
  exit /b 1
)

echo [5/6] 启动 D 盘 PostgreSQL 服务...
net start "%SVC%" >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [WARN] net start 失败，改用 pg_ctl...
  "%PG_BIN%\pg_ctl.exe" -D "%PG_DATA_D%" -l "%PG_DATA_D%\pg_migrate_start.log" start >> "%LOG%" 2>&1
)

echo [6/6] 等待就绪...
set OK=0
for /L %%i in (1,1,60) do (
  if exist "%PG_DATA_D%\postmaster.pid" (
    "%PG_BIN%\pg_isready.exe" -h 127.0.0.1 -p 5432 >nul 2>&1
    if not errorlevel 1 (
      set OK=1
      goto :ready
    )
  )
  timeout /t 3 /nobreak >nul
)

:ready
echo.
if "%OK%"=="1" (
  echo [OK] PostgreSQL 已在 D 盘运行
  type "%PG_DATA_D%\postmaster.pid"
  echo.
  sc qc "%SVC%" | findstr BINARY_PATH_NAME
) else (
  echo [FAIL] D 盘启动未就绪，请看:
  echo   %LOG%
  echo   %PG_DATA_D%\log
  echo   %PG_DATA_D%\pg_migrate_start.log
)
echo.
echo 日志: %LOG%
echo.
pause
