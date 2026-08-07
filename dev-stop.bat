@echo off
chcp 65001 > nul
title Alpha Arena Dev - Stop
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\stop-dev.ps1" %*
if errorlevel 1 pause
