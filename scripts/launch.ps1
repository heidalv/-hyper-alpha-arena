# 001Alpha Launcher (PowerShell)
$ROOT = "D:\BaiduNetdiskDownload\001Alpha-Windows迁移包-20260610-2228\001Alpha\Hyper-Alpha-Arena"
$VAULT = Join-Path $ROOT "obsidian_vault"
$LOG_DIR = Join-Path $ROOT "logs"

Write-Host "=============================================="
Write-Host " 001Alpha Launcher"
Write-Host "=============================================="
Write-Host "Root: $ROOT"
Write-Host ""

$null = New-Item -ItemType Directory -Path $LOG_DIR -Force -ErrorAction SilentlyContinue

# Start a process with good Unicode handling
function Start-Daemon {
    param($Name, $RelScript)
    $log = Join-Path $LOG_DIR "$Name.log"
    $err = Join-Path $LOG_DIR "$Name.err.log"
    Write-Host "  Starting $Name..."
    try {
        # Method 1: Start-Process with explicit parameters
        Start-Process -FilePath "python" -ArgumentList $RelScript -WorkingDirectory $ROOT -WindowStyle Hidden `
            -RedirectStandardOutput $log -RedirectStandardError $err -ErrorAction Stop
        Write-Host "  $Name started."
    } catch {
        Write-Host "  Method 1 failed, trying Method 2..."
        try {
            # Method 2: Start-Job (creates a separate PowerShell process)
            Start-Job -Name $Name -ScriptBlock {
                param($d, $s)
                Set-Location $d
                python $s
            } -ArgumentList $ROOT, $RelScript | Out-Null
            Write-Host "  $Name started (via job)."
        } catch {
            Write-Host "  ERROR: $_"
        }
    }
}

# 0. Python check
try { & python --version } catch { Write-Host "ERROR: python not in PATH!"; Read-Host; exit }

# 1. Backend
$bkRunning = $false
try { $null = Invoke-WebRequest -Uri "http://localhost:8000/health" -TimeoutSec 2 -UseBasicParsing -ErrorAction Stop; $bkRunning = $true } catch {}
if ($bkRunning) { Write-Host "[1/4] Backend: already running" } else { Write-Host "[1/4] Backend:"; Start-Daemon "backend" "backend\start_server.py" }

# 2. Bridge
$brRunning = $false
try { $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue; foreach ($p in $procs) { if ($p.CommandLine -match "obsidian_bridge") { $brRunning = $true; break } } } catch {}
if ($brRunning) { Write-Host "[2/4] Bridge: already running" } else { Write-Host "[2/4] Bridge:"; Start-Daemon "bridge" "backend\services\obsidian_bridge.py" }

# 3. Obsidian
Write-Host "[3/4] Obsidian..."
try { Start-Process "obsidian://open?vault=obsidian_vault" -ErrorAction Stop; Write-Host "  OK" } catch { Start-Process "$env:LOCALAPPDATA\Obsidian\Obsidian.exe" -ArgumentList $VAULT }

# 4. Plugins
Write-Host "[4/4] Dataview..."
$dv = Join-Path $VAULT ".obsidian\plugins\dataview\manifest.json"
if (Test-Path $dv) {
    $v = (Get-Content $dv | ConvertFrom-Json).version
    Write-Host "  Dataview v$v installed"
} else {
    Write-Host "  NOT installed. Run: setup_obsidian_plugins.ps1"
}

Write-Host "`n======= Done ======="
Read-Host "Press Enter"