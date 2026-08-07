<#
.SYNOPSIS
    Print current status of Hyper-Alpha-Arena dev runtime:
    which ports are listening, which PIDs are ours, where logs live.

.DESCRIPTION
    Purely read-only. Safe to run at any time.
#>
[CmdletBinding()]
param(
    [int[]]$Ports = @(5173, 5174, 5175, 8000, 8001)
)

$ErrorActionPreference = 'SilentlyContinue'
$RepoRoot = Split-Path $PSScriptRoot -Parent
$LogDir = Join-Path $RepoRoot 'logs'

function Write-Section($t) {
    Write-Host ""
    Write-Host "==== $t ====" -ForegroundColor Cyan
}

Write-Section "1) uvicorn backend"
$back = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -match 'uvicorn' -and $_.CommandLine -match 'backend\.main:app' }
if ($back) {
    foreach ($p in $back) {
        $m = $null
        if ($p.CommandLine -match '--port\s+(\d+)') { $m = $matches[1] }
        Write-Host ("  [UP  ] pid={0,-6} port={1,-5} started={2}" -f $p.ProcessId, $m, $p.CreationDate) -ForegroundColor Green
    }
} else {
    Write-Host "  [DOWN] no uvicorn process" -ForegroundColor Yellow
}

Write-Section "2) vite frontend"
$front = Get-CimInstance Win32_Process -Filter "Name='node.exe'" |
    Where-Object {
        ($_.CommandLine -match 'vite' -or $_.CommandLine -match 'npm.*run.*dev') -and
         $_.CommandLine -match 'Hyper-Alpha-Arena'
    }
if ($front) {
    foreach ($p in $front) {
        Write-Host ("  [UP  ] pid={0,-6} started={1}" -f $p.ProcessId, $p.CreationDate) -ForegroundColor Green
    }
} else {
    Write-Host "  [DOWN] no vite process" -ForegroundColor Yellow
}

Write-Section "3) ports"
foreach ($port in $Ports) {
    $c = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    if ($c) {
        $pid_ = $c.OwningProcess | Select-Object -First 1
        $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$pid_"
        $tag = if ($proc) { $proc.Name } else { '?' }
        Write-Host ("  port {0}  pid={1}  [{2}]" -f $port, $pid_, $tag) -ForegroundColor Green
    } else {
        Write-Host ("  port {0}  free" -f $port) -ForegroundColor DarkGray
    }
}

Write-Section "4) logs"
foreach ($f in @('backend.log', 'frontend.log', 'launcher.log')) {
    $path = Join-Path $LogDir $f
    if (Test-Path $path) {
        $size = (Get-Item $path).Length
        $mtime = (Get-Item $path).LastWriteTime
        Write-Host ("  {0,-15} {1,8:N0} bytes  mtime={2}" -f $f, $size, $mtime) -ForegroundColor DarkCyan
    } else {
        Write-Host ("  {0,-15} (not created yet)" -f $f) -ForegroundColor DarkGray
    }
}

Write-Host ""
Write-Host "URLs (if up):  http://127.0.0.1:5173   http://127.0.0.1:8000/docs" -ForegroundColor Cyan
