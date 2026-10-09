@echo off
rem [h564 2026-09-29] Auto-accept guard: after a trial's verdict lands, append the
rem acceptance block to 研究结论/三件事验收_20260929.md automatically (h546 --append).
rem Runs every 30 min via task DSH_HFT_AUTO_ACCEPT.
rem Why: the last objective step ("record acceptance") used to require a human/session
rem to run h546 by hand -- the verdicts at 21:48 / next day could land with nobody there.
rem It never writes dry-run artifacts (dry_run=true is excluded) and is idempotent
rem (the acceptance block is replaced, not duplicated). Log: logs/auto_accept.log
rem To stop it: schtasks /change /tn "DSH_HFT_AUTO_ACCEPT" /disable
rem IMPORTANT: keep this file ASCII-only (cmd.exe parses .cmd in the OEM codepage).
cd /d D:\001Alpha\Hyper-Alpha-Arena
".venv\Scripts\python.exe" scripts\h564_auto_accept.py --apply
exit /b %ERRORLEVEL%
