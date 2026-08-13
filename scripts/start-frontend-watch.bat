@echo off
rem Start the auto-publish watcher for the desktop frontend.
rem Any change under frontend-next/src or frontend-next/electron will, after a
rem quiet debounce window, bump the version, rebuild, publish and notify clients.
cd /d "%~dp0.."
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0watch-frontend-publish.ps1"
