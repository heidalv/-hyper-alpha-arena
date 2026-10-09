@echo off
REM ============================================================
REM MM lane offline researcher (h650) - one-shot task launcher.
REM Called by scheduled task DSH_MM_RESEARCHER every 4 hours.
REM
REM Zero authority: the only side effect is appending one line to
REM data/mm_researcher_queue.jsonl. It never touches quotes or params.
REM
REM keep this file pure ASCII: non-ASCII comments get mangled by
REM cmd.exe under the GBK code page and leak out as stray commands.
REM ============================================================
set PYTHONIOENCODING=utf-8
cd /d "D:\001Alpha\Hyper-Alpha-Arena"
".venv\Scripts\python.exe" "scripts\mm_researcher_once.py" >> "logs\mm_researcher.log" 2>&1
