$ROOT = "D:\001Alpha\Hyper-Alpha-Arena"
$VAULT = Join-Path $ROOT "obsidian_vault"
$LOG_DIR = Join-Path $ROOT "logs"
$null = New-Item -ItemType Directory -Path $LOG_DIR -Force -ErrorAction SilentlyContinue

Write-Host "=============================================="
Write-Host " 001Alpha Launcher (via junction)"
Write-Host "=============================================="
Write-Host "Root: $ROOT`n"

try { & python --version } catch { Write-Host "ERROR: python not in PATH!"; Read-Host; exit }

$bk = $false
try { $null = Invoke-WebRequest -Uri "http://localhost:8000/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop; $bk = $true } catch {}
if ($bk) { Write-Host "[1/4] Backend: OK" } else { Write-Host "[1/4] Backend: starting..."; Start-Process python -ArgumentList "backend\start_server.py" -WorkingDirectory $ROOT -WindowStyle Hidden; Write-Host "  started" }

$br = $false
try { Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match "obsidian_bridge" } | ForEach-Object { $br = $true } } catch {}
if ($br) { Write-Host "[2/4] Bridge: OK" } else { Write-Host "[2/4] Bridge: starting..."; Start-Process python -ArgumentList "backend\services\obsidian_bridge.py" -WorkingDirectory $ROOT -WindowStyle Hidden; Write-Host "  started" }

Write-Host "[3/4] Obsidian..."; Start-Process "obsidian://open?vault=obsidian_vault"; Write-Host "  OK"

Write-Host "[4/4] Dataview..."
$dv = Join-Path $VAULT ".obsidian\plugins\dataview\manifest.json"
if (Test-Path $dv) { $v = (Get-Content $dv | ConvertFrom-Json).version; Write-Host "  Dataview v$v installed" }

Write-Host "`n======= Done ======="
Read-Host "Press Enter"