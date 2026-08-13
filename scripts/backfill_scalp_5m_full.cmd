@echo off
cd /d D:\001Alpha\Hyper-Alpha-Arena
if not exist reports\scalp_backfill mkdir reports\scalp_backfill
".venv\Scripts\python.exe" scripts\backfill_scalp_5m.py --days 60 --exchange asterdex > reports\scalp_backfill\backfill_full_console.log 2>&1
echo EXIT=%ERRORLEVEL% >> reports\scalp_backfill\backfill_full_console.log
