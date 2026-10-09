@echo off
REM ============================================================
REM AI coin selection - HFT (make-market) lane, auto universe.
REM Called by scheduled task DSH_HFT_UNIVERSE_SELECT every 30 min.
REM
REM Why a wrapper .bat instead of putting the command in schtasks /TR:
REM   schtasks /TR is capped at 261 characters. This command needs
REM   env var + cd + quoted long paths + redirection, which exceeds it
REM   (observed: "cannot be more than 261 character(s)"). Worse, if you
REM   /Delete first and then /Create fails, the task is simply LOST.
REM
REM Why PYTHONIOENCODING=utf-8 is mandatory:
REM   a scheduled task's stdout defaults to GBK (cp936) on this host.
REM   Any non-GBK character in the script's output raises
REM   UnicodeEncodeError and ABORTS the script mid-way (observed: the
REM   DB write step was skipped entirely because printing crashed first).
REM
REM NOTE: keep this file pure ASCII. Non-ASCII comments get mangled by
REM   cmd.exe under the GBK code page and leak out as stray commands.
REM ============================================================
set PYTHONIOENCODING=utf-8
cd /d "D:\001Alpha\Hyper-Alpha-Arena"
".venv\Scripts\python.exe" "scripts\hft_universe_select.py" --lane mm_asterdex >> "logs\hft_universe_select.log" 2>&1
