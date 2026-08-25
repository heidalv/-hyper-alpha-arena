@echo off
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Select-Object ProcessId, CommandLine | Out-String -Width 200"
