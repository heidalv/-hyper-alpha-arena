@echo off
REM Silent wrapper for the depth watchdog (called via run-hidden.vbs).
REM keep pure ASCII.
set PYTHONIOENCODING=utf-8
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "D:\001Alpha\Hyper-Alpha-Arena\scripts\aster-depth-watchdog.ps1" -Once
