# Publish desktop EXE into local update feed (releases/desktop)
# Usage (repo root):
#   powershell -ExecutionPolicy Bypass -File scripts\publish-desktop.ps1
#   powershell -ExecutionPolicy Bypass -File scripts\publish-desktop.ps1 -SkipBuild
#   powershell -ExecutionPolicy Bypass -File scripts\publish-desktop.ps1 -Version 0.2.1

param(
  [string]$Version = "",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$FeDir = Join-Path $RepoRoot "frontend-next"
$PkgJson = Join-Path $FeDir "package.json"
$DistDir = Join-Path $FeDir "dist-electron"
$OutDir = Join-Path $RepoRoot "releases\desktop"

Write-Host ""
Write-Host "=== AlphaArena Desktop Publish ===" -ForegroundColor Cyan
Write-Host "repo: $RepoRoot"

if (-not (Test-Path $PkgJson)) {
  throw "missing frontend-next/package.json"
}

if ($Version) {
  $raw = Get-Content $PkgJson -Raw -Encoding UTF8
  $raw = $raw -replace '"version"\s*:\s*"[^"]+"', ('"version": "' + $Version + '"')
  # 必须写无 BOM 的 UTF-8：Set-Content -Encoding UTF8 在 Windows PowerShell 5.1
  # 会加 BOM，Next/Turbopack 读取 package.json 时会报 JSON 解析错误（1:1）。
  [System.IO.File]::WriteAllText(
    $PkgJson,
    $raw,
    (New-Object System.Text.UTF8Encoding($false))
  )
  Write-Host "[version] set to $Version"
}

$pkg = Get-Content $PkgJson -Raw -Encoding UTF8 | ConvertFrom-Json
$ver = [string]$pkg.version
Write-Host "[version] current $ver"

# ── 版本单调守卫：新版本必须严格高于当前 feed 版本 ──
# electron-updater 只在 feed 版本 > 本机版本时触发更新；同版本重发会让远程端
# 永远显示“已是最新”。这里在发布前直接失败，避免静默产生无效更新包。
$latestYml = Join-Path $OutDir "latest.yml"
if (Test-Path $latestYml) {
    $prevRaw = Get-Content $latestYml -Raw -Encoding UTF8
    if ($prevRaw -match '(?m)^version:\s*([0-9A-Za-z.\-+]+)') {
        $prevVer = $Matches[1].Trim()
        $aParts = @($ver -split '\.')
        $bParts = @($prevVer -split '\.')
        $maxLen = [Math]::Max($aParts.Count, $bParts.Count)
        $isGreater = $false
        for ($i = 0; $i -lt $maxLen; $i++) {
            $av = 0
            $bv = 0
            if ($i -lt $aParts.Count) {
                if ($aParts[$i] -match '^\d+$') { $av = [int]$aParts[$i] } else { $av = -1 }
            }
            if ($i -lt $bParts.Count) {
                if ($bParts[$i] -match '^\d+$') { $bv = [int]$bParts[$i] } else { $bv = -1 }
            }
            if ($av -gt $bv) { $isGreater = $true; break }
            if ($av -lt $bv) { break }
        }
        if (-not $isGreater) {
            throw "发布被阻止：新版本 $ver 必须高于当前 feed 版本 $prevVer。请用 -Version 指定更高版本（如 0.2.1），否则远程端会一直显示已是最新。"
        }
    }
}

if (-not $SkipBuild) {
  Write-Host "[build] next build + electron-builder ..."
  Push-Location $FeDir
  try {
    $env:CSC_IDENTITY_AUTO_DISCOVERY = "false"
    $env:NEXT_PUBLIC_VERSION = $ver
    # Next 16 会向 stderr 输出非致命告警（如 output:export 的 rewrites 提示），
    # 在 ErrorActionPreference=Stop 下会被 PS 5.1 包装成 NativeCommandError 并中断
    # 构建。这里临时放宽为 Continue，只以 $LASTEXITCODE 判失败。
    $ErrorActionPreference = "Continue"
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "next build failed exit=$LASTEXITCODE" }
    # 直接调 node 里的 electron-builder，避免 npm.ps1 包装器偶发 exit=-1
    # 却无任何错误输出（实测打包阶段静默失败，node 直连可稳定完成）。
    node ".\node_modules\electron-builder\out\cli\cli.js" --win nsis
    $ErrorActionPreference = "Stop"
    if ($LASTEXITCODE -ne 0) { throw "electron:build failed exit=$LASTEXITCODE" }
  } finally {
    $ErrorActionPreference = "Stop"
    Pop-Location
  }
} else {
  Write-Host "[build] skipped (-SkipBuild), copy existing dist-electron"
}

if (-not (Test-Path $DistDir)) {
  throw "missing dist dir: $DistDir"
}

New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

Get-ChildItem $OutDir -File | Where-Object {
  $_.Name -like "AlphaArena Setup*.exe" -or
  $_.Name -like "*.blockmap" -or
  $_.Name -eq "latest.yml" -or
  $_.Name -eq "builder-debug.yml"
} | ForEach-Object {
  Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
}

$copied = @()
foreach ($name in @("latest.yml", "builder-debug.yml")) {
  $src = Join-Path $DistDir $name
  if (Test-Path $src) {
    Copy-Item $src (Join-Path $OutDir $name) -Force
    $copied += $name
  }
}

Get-ChildItem $DistDir -File | Where-Object {
  $_.Name -eq "AlphaArena Setup $ver.exe" -or
  $_.Name -eq "AlphaArena Setup $ver.exe.blockmap"
} | ForEach-Object {
  Copy-Item $_.FullName (Join-Path $OutDir $_.Name) -Force
  $copied += $_.Name
}

# Fallback: if versioned name missing, copy any Setup (prefer highest)
if (-not ($copied | Where-Object { $_ -like "AlphaArena Setup*.exe" })) {
  $setup = Get-ChildItem $DistDir -File | Where-Object { $_.Name -like "AlphaArena Setup*.exe" } |
    Sort-Object Name -Descending | Select-Object -First 1
  if ($setup) {
    Copy-Item $setup.FullName (Join-Path $OutDir $setup.Name) -Force
    $copied += $setup.Name
    $bm = "$($setup.FullName).blockmap"
    if (Test-Path $bm) {
      Copy-Item $bm (Join-Path $OutDir "$($setup.Name).blockmap") -Force
      $copied += "$($setup.Name).blockmap"
    }
  }
}

if (-not (Test-Path (Join-Path $OutDir "latest.yml"))) {
  throw "publish failed: releases/desktop/latest.yml missing"
}

Write-Host ""
Write-Host "[ok] published to releases/desktop/:" -ForegroundColor Green
$copied | ForEach-Object { Write-Host "  - $_" }

# ── 通知后端：向已连接的桌面端广播新版本（web 客户端不接收） ──
try {
    $apiKey = ""
    $envFile = Join-Path $RepoRoot ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile -Encoding UTF8 | Where-Object { $_ -match '^BACKEND_API_KEY\s*=' } | Select-Object -First 1
        if ($line) { $apiKey = ($line -split '=', 2)[1].Trim() }
    }
    $headers = @{}
    if ($apiKey) { $headers["X-API-Key"] = $apiKey }
    $payload = @{ version = $ver; path = "AlphaArena Setup $ver.exe" } | ConvertTo-Json -Compress
    $resp = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8000/api/desktop/publish-notify" `
        -ContentType "application/json" -Headers $headers -Body $payload -TimeoutSec 8
    if ($resp.ok) {
        Write-Host "[notify] desktop_update broadcast sent (version=$ver)" -ForegroundColor Green
    } else {
        Write-Host "[notify] skipped: $($resp.error)" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[notify] backend not reachable, skipped: $($_.Exception.Message)" -ForegroundColor Yellow
}

$tsIp = ""
try { $tsIp = (& tailscale ip -4 2>$null | Select-Object -First 1).Trim() } catch { }

Write-Host ""
Write-Host "Update feed URLs (use same backend in login page):" -ForegroundColor Yellow
Write-Host "  http://127.0.0.1:8000/arena-updates/latest.yml"
if ($tsIp) {
  Write-Host "  http://${tsIp}:8000/arena-updates/latest.yml"
}
Write-Host "  GET http://127.0.0.1:8000/api/desktop/version"
Write-Host ""
Write-Host "Note: backend must listen on 0.0.0.0:8000 for Tailscale clients."
Write-Host ""
