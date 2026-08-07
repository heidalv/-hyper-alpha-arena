@echo off
chcp 65001 >nul
title 001Alpha - Stop All Services
setlocal

echo ==============================================
echo  Stopping 001Alpha Services
echo ==============================================
echo.

echo [1/2] Stopping Obsidian Bridge...
for /f "tokens=2 delims=," %%a in ('tasklist /FI "WINDOWTITLE eq 001Alpha-Bridge" /FO CSV /NH 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)
echo   Bridge stopped.
echo.

echo [2/2] Stopping Backend...
for /f "tokens=2 delims=," %%a in ('tasklist /FI "WINDOWTITLE eq 001Alpha-Backend" /FO CSV /NH 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)
echo   Backend stopped.
echo.

echo ==============================================
echo  All services stopped.
echo ==============================================
echo.
pause