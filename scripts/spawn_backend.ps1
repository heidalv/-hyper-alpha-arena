$env:BACKEND_PORT = '8000'
$env:BACKEND_HOST = '0.0.0.0'
$env:NO_RELOAD = 'true'
$env:DATA_CENTER_MODE = 'standalone'
$env:PYTHONUNBUFFERED = '1'
Start-Process -FilePath 'D:\001Alpha\Hyper-Alpha-Arena\backend\.venv\Scripts\python.exe' -ArgumentList 'scripts/run_uvicorn_dev.py' -WorkingDirectory 'D:\001Alpha\Hyper-Alpha-Arena' -RedirectStandardOutput 'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.log' -RedirectStandardError 'D:\001Alpha\Hyper-Alpha-Arena\logs\backend.error.log' -WindowStyle Hidden
Write-Output 'SPAWNED'
