# Silent frontend port guard for DSH_FE_DEV_WATCHDOG.
# Replaces start-frontend-guarded.cmd so Task Scheduler never allocates a console.
$ErrorActionPreference = 'SilentlyContinue'
$Root = Split-Path $PSScriptRoot -Parent
$Log = Join-Path $Root 'logs\frontend-launcher.log'
$Port = 5273

function Write-FeLog([string]$msg) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Add-Content -Path $Log -Value $line -Encoding UTF8
}

$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    Write-FeLog "[start-frontend] port $Port already LISTENING - skip start"
    exit 0
}

Write-FeLog "[start-frontend] port $Port free - starting npm run dev (hidden vbs)"
# [h773 2026-10-03] 原来用 `Start-Process cmd.exe ... -WindowStyle Hidden` ——
# **cmd.exe 一定分配控制台,而 -WindowStyle Hidden 对控制台程序不生效** ⇒ 每次重启弹黑框。
# 改走 start-frontend-hidden.vbs(WScript.Shell.Run(cmd,0) 的 SW_HIDE 有效)。
$vbs = Join-Path $Root 'scripts\start-frontend-hidden.vbs'
if (Test-Path $vbs) {
    Start-Process -FilePath 'wscript.exe' -ArgumentList '//B', '//Nologo', $vbs -WindowStyle Hidden
}
exit 0
