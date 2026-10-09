@echo off
rem [h556 2026-09-29] Auto node-switch guard for the market-data tunnel.
rem Runs every 10 min via task DSH_HFT_AUTO_SWITCH.
rem Acts ONLY when the TLS tunnel to fapi/fstream has failed twice in a row,
rem at most 3 times per 6 hours, and logs everything to logs/auto_switch.log.
rem It touches the Shadowsocks node selection (only while that line is already
rem dead). To stop it: schtasks /change /tn "DSH_HFT_AUTO_SWITCH" /disable
rem IMPORTANT: keep this file ASCII-only (cmd.exe parses .cmd in the OEM codepage).
cd /d D:\001Alpha\Hyper-Alpha-Arena
".venv\Scripts\python.exe" scripts\h556_auto_switch.py --apply
exit /b %ERRORLEVEL%
