@echo off
cd /d D:\001Alpha\Hyper-Alpha-Arena\frontend-next
if not exist ..\logs mkdir ..\logs
call npm run dev >> ..\logs\frontend-next.log 2>&1
