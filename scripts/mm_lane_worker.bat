@echo off
REM ============================================================
REM MM lane tick worker (F283 external ticker) - idempotent launcher.
REM Called by scheduled task DSH_MM_WORKER every 5 minutes.
REM
REM The worker itself is a long-running loop with a single-instance
REM lock (logs/mm_lane_worker.lock). If another instance is alive it
REM exits immediately, so running this every 5 minutes is safe and
REM acts as a watchdog: if the worker died, this restarts it.
REM
REM Why this task is REQUIRED:
REM   .env has MM_LANE_TICKER=external => the API backend does NOT
REM   register the in-process tick (to avoid double ticking). Without
REM   this worker the lane gets ZERO ticks and silently does nothing
REM   (observed: 90s with ticks=0 while status=active).
REM
REM keep this file pure ASCII: non-ASCII comments get mangled by
REM cmd.exe under the GBK code page and leak out as stray commands.
REM ============================================================
set PYTHONIOENCODING=utf-8
REM [h742 2026-10-03] enable lane-level risk gates (daily loss stop).
REM vol_pause_sigma is set to 0 in the registry so only the daily-loss
REM gate is live (equity 284 x 0.7pct = -2U fuse).
set MM_LANE_LIMITS_ENFORCE=1
cd /d "D:\001Alpha\Hyper-Alpha-Arena"
".venv\Scripts\python.exe" "scripts\mm_lane_worker.py" >> "logs\mm_lane_worker.out.log" 2>&1
