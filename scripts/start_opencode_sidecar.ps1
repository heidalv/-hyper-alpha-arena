# Start OpenCode Sidecar - DeepSeek in opencode.json, key from .env
# [2026-09-05] 加固版：计划任务非交互会话下曾出现 exit 1 且无日志（02:59 事故）。
# 改动：①守卫用 TCP 探测替代 Get-NetTCPConnection（更少依赖、更快）；
#       ②全程 try/catch，任何失败都落到任务日志；③serve 退出后显式记录并 exit 其码。
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $Root ".env"
$TaskLog = Join-Path $Root "logs\opencode_sidecar_task.log"
$port = 4096

function Write-TaskLog([string]$msg) {
    try { "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Out-File -Append -Encoding utf8 $TaskLog } catch {}
}

try {
    if (Test-Path $EnvFile) {
        Get-Content $EnvFile | ForEach-Object {
            if ($_ -match '^\s*#' -or $_ -match '^\s*$') { return }
            $pair = $_ -split '=', 2
            if ($pair.Count -eq 2) {
                try { Set-Item -Path ("Env:" + $pair[0].Trim()) -Value $pair[1].Trim() } catch {}
            }
        }
        if ($env:OPENCODE_PORT) { $port = [int]$env:OPENCODE_PORT }
        Write-TaskLog ("env loaded, ZAI key present=" + [bool]$env:ZAI_CODING_PLAN_API_KEY)
    }

    # 幂等守卫：TCP 连通即视为已在线（计划任务每 5 分钟重入 + 手动启动共存安全）
    $client = New-Object Net.Sockets.TcpClient
    $alive = $false
    try {
        $task = $client.ConnectAsync('127.0.0.1', $port)
        if ($task.Wait(800) -and $client.Connected) { $alive = $true }
    } catch { $alive = $false } finally { $client.Dispose() }
    if ($alive) {
        Write-TaskLog "skip: port $port already listening"
        exit 0
    }

    $OpencodeExe = $null
    $npmExe = Join-Path $env:APPDATA "npm\node_modules\opencode-ai\bin\opencode.exe"
    if ($npmExe -and (Test-Path $npmExe)) {
        $OpencodeExe = $npmExe
    } elseif (Get-Command opencode -ErrorAction SilentlyContinue) {
        $OpencodeExe = (Get-Command opencode).Source
    }
    if (-not $OpencodeExe) {
        Write-TaskLog "ERROR: opencode CLI not found (APPDATA=$env:APPDATA)"
        exit 1
    }

    Set-Location $Root
    Write-TaskLog "starting sidecar on port $port (exe=$OpencodeExe)"
    & $OpencodeExe serve --port $port --hostname 127.0.0.1 2>&1 | ForEach-Object { Out-File -Append -Encoding utf8 $TaskLog -InputObject $_ }
    $code = $LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    Write-TaskLog "sidecar exited with code $code"
    exit $code
} catch {
    Write-TaskLog ("FATAL: " + ($_ | Out-String))
    exit 1
}
