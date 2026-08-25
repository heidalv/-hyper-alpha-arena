@echo off
rem Alpha-Arena: 每周日凌晨 01:00 独立进程跑每周 NSGA-II 进化（不占端口，不受 DSH 监管）
cd /d D:\001Alpha\Hyper-Alpha-Arena
backend\.venv\Scripts\python.exe scripts\run_weekly_evolution_standalone.py >> logs\standalone_weekly_scheduled.log 2>&1
