@echo off
rem [h536 2026-09-29] Lane health alarm entry point (scheduled task DSH_HFT_LANE_ALARM, every 10 min).
rem Returns 2 when CRITICAL so the task Last Result becomes non-zero and any
rem inspection/daily digest can see it.
rem Incident background: 04:32 the shadowsocks upstream stopped forwarding ->
rem the market WS ingester looped on reconnect -> the lane produced ZERO legs for
rem 4.2 hours with NO alert (the h472 monitor only tracks taker mix, not silence).
rem IMPORTANT: keep this file ASCII-only. cmd.exe parses .cmd in the OEM codepage,
rem so UTF-8 Chinese comments get mangled and their fragments run as commands.
cd /d D:\001Alpha\Hyper-Alpha-Arena
".venv\Scripts\python.exe" scripts\h536_lane_alarm.py --quiet
exit /b %ERRORLEVEL%
