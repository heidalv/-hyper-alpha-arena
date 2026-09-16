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
        # [调研轮12 2026-09-16 根因修复] 原实现用 `Set-Item Env:` 逐行注入，实测
        # `ZAI key present=False` —— 而 opencode.json 的 zai provider 是
        # `options.apiKey = {env:ZAI_CODING_PLAN_API_KEY}`，key 必须来自**进程环境**。
        # 结果：sidecar 每个 GLM 请求都拿不到凭据，z.ai 返回
        #   {"error":{"code":"1001","message":"Authentication parameter not received in Header"}}
        # → 响应 parts 为空 → 该票 3 秒失败（MiniMax 顶上），表现为「GLM 持续不稳」。
        # 改为 .NET 显式以 UTF-8 读取 + SetEnvironmentVariable('Process')，并逐键回读校验。
        $lines = [System.IO.File]::ReadAllLines($EnvFile, [System.Text.Encoding]::UTF8)
        $applied = 0
        foreach ($line in $lines) {
            if ($line -match '^\s*#' -or $line -match '^\s*$') { continue }
            $idx = $line.IndexOf('=')
            if ($idx -le 0) { continue }
            $k = $line.Substring(0, $idx).Trim()
            $v = $line.Substring($idx + 1).Trim()
            if ($k -match '^[A-Za-z_][A-Za-z0-9_]*$' -and $v.Length -gt 0) {
                try {
                    [System.Environment]::SetEnvironmentVariable($k, $v, 'Process')
                    $applied++
                } catch {}
            }
        }
        if ($env:OPENCODE_PORT) { $port = [int]$env:OPENCODE_PORT }
        $zai = [System.Environment]::GetEnvironmentVariable('ZAI_CODING_PLAN_API_KEY', 'Process')
        Write-TaskLog ("env loaded ($applied keys), ZAI key present=" + [bool]$zai + " len=" + ($(if ($zai) { $zai.Length } else { 0 })))
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
    # [调研轮12 2026-09-16] 预载补丁抬高事件监听器上限：根治「每请求泄漏监听器 →
    # 撞 EventTarget 上限 10 → 进程崩溃」，从而不再依赖「每 8 次调用重建 sidecar」
    # 的规避手段（那正是 GLM 每天约 1 小时不可用的根因）。
    $patchJs = Join-Path $PSScriptRoot "opencode_patch_maxlisteners.js"
    if (Test-Path $patchJs) {
        $env:NODE_OPTIONS = "--require `"$patchJs`""
        Write-TaskLog "NODE_OPTIONS=$($env:NODE_OPTIONS)"
    } else {
        Write-TaskLog "WARN: patch js not found: $patchJs"
    }
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
