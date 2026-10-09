@echo off
rem [h665] LIVE market-making lane worker (ASCII only - cmd OEM codepage safe)
rem lane=mm_asterdex_live, heartbeat/lock/log in logs\live, isolated from paper.
rem Bridge is no-op until Asterdex API Key is configured and lane is active.
chcp 65001 >nul
set MM_LANE_WORKER_LANE=mm_asterdex_live
set MM_LANE_WORKER_LOG_DIR=logs\live
"D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\python.exe" "D:\001Alpha\Hyper-Alpha-Arena\scripts\mm_lane_worker.py"
