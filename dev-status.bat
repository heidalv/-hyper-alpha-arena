@echo off
chcp 65001 > nul
title Alpha Arena Dev - Status
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\status-dev.ps1" %*
pause
