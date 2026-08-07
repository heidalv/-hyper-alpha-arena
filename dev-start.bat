@echo off
chcp 65001 > nul
title Alpha Arena Dev - Start
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File ".\scripts\start-dev.ps1" %*
if errorlevel 1 (
    echo.
    echo [ERROR] dev-start 失败，见日志 logs\backend.log / logs\frontend.log
    pause
)
