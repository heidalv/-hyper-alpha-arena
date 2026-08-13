# OpenCode setup - DeepSeek configured in opencode.json, key from .env to Sidecar
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Alpha Arena OpenCode Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

$EnvFile = Join-Path $Root ".env"
if (-not (Test-Path $EnvFile)) {
    Write-Host "[FAIL] missing .env - copy from .env.example and set DEEPSEEK_API_KEY" -ForegroundColor Red
    exit 1
}
Get-Content $EnvFile | ForEach-Object {
    if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
    $pair = $_ -split '=', 2
    if ($pair.Count -eq 2) {
        Set-Item -Path "Env:$($pair[0].Trim())" -Value $pair[1].Trim()
    }
}
Write-Host "[OK] loaded .env"

$OpencodeExe = $null
$npmExe = Join-Path $env:APPDATA "npm\node_modules\opencode-ai\bin\opencode.exe"
if (Test-Path $npmExe) { $OpencodeExe = $npmExe }
elseif (Get-Command opencode -ErrorAction SilentlyContinue) { $OpencodeExe = "opencode" }
if (-not $OpencodeExe) {
    Write-Host "[FAIL] opencode CLI not found. Run: npm install -g opencode-ai" -ForegroundColor Red
    exit 1
}
$ver = & $OpencodeExe --version 2>$null
Write-Host "[OK] opencode CLI $ver"

if (-not $env:DEEPSEEK_API_KEY -or $env:DEEPSEEK_API_KEY.Length -lt 8) {
    Write-Host "[FAIL] DEEPSEEK_API_KEY not set in .env" -ForegroundColor Red
    exit 1
}
Write-Host "[OK] DEEPSEEK_API_KEY set (len=$($env:DEEPSEEK_API_KEY.Length))"

$required = @(
    "opencode.json",
    ".opencode\agents\plan.md",
    "backend\prompts\opencode_analysis_system.md"
)
foreach ($rel in $required) {
    if (-not (Test-Path (Join-Path $Root $rel))) {
        Write-Host "[FAIL] missing $rel" -ForegroundColor Red
        exit 1
    }
}
Write-Host "[OK] OpenCode config files present"

$envContent = Get-Content $EnvFile -Raw
$defaults = @{
    "OPENCODE_ENABLED"           = "true"
    "OPENCODE_SERVER_URL"        = "http://127.0.0.1:4096"
    "OPENCODE_AGENT_PLAN"        = "plan"
    "OPENCODE_MODEL"             = "deepseek/deepseek-v4-flash"
    "OPENCODE_SMALL_MODEL"       = "deepseek/deepseek-v4-flash"
    "OPENCODE_BRIDGE_TRANSPORT"  = "http"
    "OPENCODE_REQUEST_TIMEOUT_S" = "180"
}
foreach ($key in $defaults.Keys) {
    if ($envContent -notmatch "(?m)^$key=") {
        Add-Content $EnvFile "`n$key=$($defaults[$key])"
        Set-Item -Path "Env:$key" -Value $defaults[$key]
        Write-Host "[ADD] .env $key=$($defaults[$key])"
    }
}
Write-Host "[OK] .env OpenCode flags ready"

$port = if ($env:OPENCODE_PORT) { [int]$env:OPENCODE_PORT } else { 4096 }
Write-Host ""
Write-Host "--- Starting OpenCode Sidecar port $port ---" -ForegroundColor Yellow
Get-NetTCPConnection -LocalPort $port -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1

$logDir = Join-Path $Root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$sidecarScript = Join-Path $PSScriptRoot "start_opencode_sidecar.ps1"
Start-Process powershell -ArgumentList @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $sidecarScript
) -WindowStyle Minimized -WorkingDirectory $Root

Write-Host "Waiting for Sidecar..."
$ready = $false
$healthUrl = "http://127.0.0.1:" + $port + "/global/health"
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $h = Invoke-RestMethod $healthUrl -TimeoutSec 3
        if ($h.healthy) { $ready = $true; break }
    } catch { }
}
if ($ready) {
    Write-Host "[OK] Sidecar running on port $port" -ForegroundColor Green
} else {
    Write-Host "[WARN] Sidecar not ready in 30s" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "--- Running verify_opencode_config.py ---" -ForegroundColor Yellow
$py = Join-Path $Root "backend\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }
& $py (Join-Path $PSScriptRoot "verify_opencode_config.py")
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " OpenCode setup complete." -ForegroundColor Green
Write-Host " Open OpenCode Center and click Analyze." -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Cyan
