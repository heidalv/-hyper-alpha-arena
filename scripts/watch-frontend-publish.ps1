param(
  [int]$DebounceSec = 180
)

$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$fe = Join-Path $repo 'frontend-next'
$log = Join-Path $repo 'logs\frontend-watch-publish.log'
$lock = Join-Path $repo 'logs\frontend-watch-publish.lock'

# single instance guard
if (Test-Path $lock) {
    $old = (Get-Content $lock -Raw -ErrorAction SilentlyContinue).Trim()
    if ($old -match '^\d+$') {
        $p = Get-Process -Id ([int]$old) -ErrorAction SilentlyContinue
        if ($p) { exit 0 }
    }
}
Set-Content -Path $lock -Value "$PID" -Encoding ASCII

function Log($msg) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $log -Value $line -Encoding UTF8
}

function Get-LatestMtime($paths) {
    $max = [datetime]::MinValue
    foreach ($p in $paths) {
        if (Test-Path $p) {
            $items = Get-Item $p -ErrorAction SilentlyContinue
            foreach ($it in $items) {
                if ($it.PSIsContainer) {
                    Get-ChildItem $it.FullName -Recurse -File -ErrorAction SilentlyContinue | ForEach-Object {
                        if ($_.LastWriteTime -gt $max) { $max = $_.LastWriteTime }
                    }
                } elseif ($it.LastWriteTime -gt $max) {
                    $max = $it.LastWriteTime
                }
            }
        }
    }
    return $max
}

function Get-NextVersion {
    $raw = Get-Content (Join-Path $fe 'package.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $v = [string]$raw.version
    $parts = @($v -split '\.')
    $idx = $parts.Count - 1
    $n = 0
    if ($parts[$idx] -match '^\d+$') { $n = [int]$parts[$idx] }
    $parts[$idx] = [string]($n + 1)
    return ($parts -join '.')
}

# Watch frontend source + electron + build configs. Never watch package.json:
# the publish pipeline itself bumps package.json, which would otherwise re-trigger us.
$watched = @(
    (Join-Path $fe 'src'),
    (Join-Path $fe 'electron'),
    (Join-Path $fe 'next.config.ts'),
    (Join-Path $fe 'postcss.config.mjs'),
    (Join-Path $fe 'electron-builder.yml')
)

$lastSeen = Get-LatestMtime $watched
$changeAt = $null
Log "watch started debounce=${DebounceSec}s pid=$PID"

try {
    while ($true) {
        Start-Sleep -Seconds 5
        $nowM = Get-LatestMtime $watched
        if ($nowM -gt $lastSeen) {
            $lastSeen = $nowM
            $changeAt = Get-Date
            Log "frontend change detected at $($nowM.ToString('yyyy-MM-dd HH:mm:ss'))"
        }
        if ($null -ne $changeAt -and ((Get-Date) - $changeAt).TotalSeconds -ge $DebounceSec) {
            $changeAt = $null
            $newVer = Get-NextVersion
            Log "quiet period passed, auto publish version=$newVer"
            try {
                $out = & (Join-Path $PSScriptRoot 'publish-desktop.ps1') -Version $newVer 2>&1
                $out | ForEach-Object { Log ("[publish] " + (([string]$_) -replace "`r`n", " ")) }
                if ($LASTEXITCODE -ne 0) {
                    Log "auto publish failed exit=$LASTEXITCODE"
                } else {
                    Log "auto publish finished version=$newVer"
                }
            } catch {
                Log "auto publish exception: $($_.Exception.Message)"
            }
        }
    }
} finally {
    Remove-Item $lock -Force -ErrorAction SilentlyContinue
    Log "watch stopped"
}
