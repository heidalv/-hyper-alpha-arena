@echo off
rem Guarded frontend dev launcher for scheduled-task watchdog use.
rem Starts `npm run dev` (next dev -p 5273) only when the port is free,
rem so a repeating task can never spawn a second dev server.
cd /d D:\001Alpha\Hyper-Alpha-Arena
netstat -ano -p tcp | findstr /R /C:"LISTENING" | findstr /C:":5273 " >nul 2>&1
if not errorlevel 1 (
    echo [%DATE% %TIME%] [start-frontend] port 5273 already LISTENING - skip start >> logs\frontend-launcher.log
    exit /b 0
)
echo [%DATE% %TIME%] [start-frontend] port 5273 free - starting >> logs\frontend-launcher.log
cd /d D:\001Alpha\Hyper-Alpha-Arena\frontend-next
call npm run dev >> ..\logs\frontend-next.log 2>&1
