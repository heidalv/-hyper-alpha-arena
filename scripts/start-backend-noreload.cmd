@echo off
setlocal enabledelayedexpansion
REM ============================================================
REM NO_RELOAD backend launcher with SINGLE-INSTANCE LOCK (2026-09-20)
REM
REM Why: this script had no instance guard. backend-watchdog.ps1 probes
REM every 30s and fires Start-Process after 5 consecutive failures
REM (~2.5 min), then sleeps 10s and keeps looping with a 90s grace window.
REM If the backend is merely SLOW (DB load, factor warmup), the probe can
REM fail 5 times while the process is still alive -> the watchdog spawns a
REM SECOND backend. Observed on 2026-09-20: two run_uvicorn_dev.py PIDs
REM (20676 then 19068/4968) coexisting after an 18:53 restart.
REM Two backends on one port means one of them fails to bind, and both
REM may race on lane_runtime_state / lane_ledger writes.
REM
REM Fix: before starting, check whether the port is already LISTENING.
REM If it is, exit without starting anything. The watchdog's next probe
REM then sees a healthy backend and resets its fail counter.
REM
REM NOTE: keep this file PURE ASCII. Chinese REM comments leak as stray
REM commands under the GBK (cp936) console of Scheduled Tasks.
REM ============================================================
cd /d D:\001Alpha\Hyper-Alpha-Arena

set NO_RELOAD=true
set DATA_CENTER_MODE=standalone
set BACKEND_PORT=8000
set BACKEND_HOST=0.0.0.0
set PYTHONIOENCODING=utf-8
REM [2026-10-10 凶手实锤] midlong brain 出进程分析经 .venv stub 派生 DSH 托管子进程,
REM 托管清理把整棵进程树(含后端)以 0xFFFFFFFF 终结(死亡陷阱 17:09:54 帧实拍)。
REM 关闭子进程模式:brain 回退进程内线程(旧行为),不再派生托管 stub。
REM 回滚:删除本行(恢复出进程分析)。
set MIDLONG_BRAIN_SUBPROCESS=0

REM --- single-instance guard: is port %BACKEND_PORT% already listening? ---
netstat -ano -p tcp | findstr /R /C:"LISTENING" | findstr /C:":%BACKEND_PORT% " >nul 2>&1
if not errorlevel 1 (
    echo [%DATE% %TIME%] [start-backend] port %BACKEND_PORT% already LISTENING - skip start >> logs\backend-launcher.log
    endlocal
    exit /b 0
)

echo [%DATE% %TIME%] [start-backend] port %BACKEND_PORT% free - starting >> logs\backend-launcher.log
REM [2026-10-10 三连杀修复] 原路径经 .venv\Scripts\python.exe(DSH 托管 stub)启动:
REM 托管 stub 被清理时后端被 0xFFFFFFFF 连坐(17:10/17:51/18:12 三帧实锤)。
REM 改为 **.runtime 解释器直跑**(与数据中台/worker 同款幸存者模式,无 stub 跳)。
REM PYTHONPATH 指向 venv site-packages 提供 uvicorn 等依赖。
set PYTHONPATH=backend\.venv\Lib\site-packages
".runtime\Python312\python.exe" "scripts\run_uvicorn_tracked.py" >> logs\backend.log 2>&1

endlocal
