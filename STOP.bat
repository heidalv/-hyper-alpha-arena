@echo off
rem ============================================================
rem  Heidalv-Alpha-Arena - Stop all services (Windows)
rem ============================================================

echo Stopping processes on port 8000 (backend) ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
    taskkill /PID %%p /T /F >nul 2>&1
)

echo Stopping processes on port 5173 (frontend) ...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":5173" ^| findstr "LISTENING"') do (
    taskkill /PID %%p /T /F >nul 2>&1
)

taskkill /FI "WINDOWTITLE eq AlphaArena-Backend*" /T /F >nul 2>&1
taskkill /FI "WINDOWTITLE eq AlphaArena-Frontend*" /T /F >nul 2>&1

echo All services stopped.
pause
