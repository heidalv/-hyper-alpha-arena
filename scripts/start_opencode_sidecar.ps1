# Start OpenCode Sidecar - DeepSeek in opencode.json, key from .env
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
        $pair = $_ -split '=', 2
        if ($pair.Count -eq 2) {
            Set-Item -Path "Env:$($pair[0].Trim())" -Value $pair[1].Trim()
        }
    }
    $keyLen = if ($env:DEEPSEEK_API_KEY) { $env:DEEPSEEK_API_KEY.Length } else { 0 }
    Write-Host "Loaded .env (DEEPSEEK_API_KEY len=$keyLen)"
}

$OpencodeExe = $null
$npmExe = Join-Path $env:APPDATA "npm\node_modules\opencode-ai\bin\opencode.exe"
if (Test-Path $npmExe) {
    $OpencodeExe = $npmExe
} elseif (Get-Command opencode -ErrorAction SilentlyContinue) {
    $OpencodeExe = (Get-Command opencode).Source
}
if (-not $OpencodeExe) {
    Write-Error "opencode CLI not found. Run: npm install -g opencode-ai"
}

$port = if ($env:OPENCODE_PORT) { $env:OPENCODE_PORT } else { 4096 }
Write-Host "Starting OpenCode Sidecar on port $port"
Write-Host "  config: $Root\opencode.json"
Write-Host "  model:  $($env:OPENCODE_MODEL)"

Set-Location $Root
& $OpencodeExe serve --port $port --hostname 127.0.0.1
