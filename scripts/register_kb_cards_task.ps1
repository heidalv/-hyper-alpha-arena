# [2026-10-10 brain-integration] Register/refresh the "knowledge card index" scheduled task
# DSH_KB_CARDS.
#
# Why a separate task: the knowledge cards (distilled from books/papers) are a STATIC file
# (backend/data/knowledge_cards.json). They do not need frequent re-indexing, so one daily
# off-peak run is enough. The index goes into the EXISTING RAG collection
# `knowledge_cards` (we do not open a second store).
#
# Schedule 05:00, staggered against existing tasks:
#   03:30 market-maker grid self-evolution (mm_evolution_mm_asterdex)
#   05:45 carry small capital
#   06:00 event study daily
#
# Usage (runs as the current user):
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_kb_cards_task.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_kb_cards_task.ps1 -Remove
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\register_kb_cards_task.ps1 -DryRun
#
# Rollback: -Remove deletes the task; MM_KB_CARDS=0 makes it a no-op. Neither touches trading.
param(
    [switch]$Remove,
    [switch]$DryRun,
    [string]$TaskName = 'DSH_KB_CARDS',
    [string]$Time = '05:00'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$quiet = Join-Path $root 'scripts\run-quiet.vbs'

if ($Remove) {
    $out = schtasks /Delete /TN $TaskName /F 2>&1
    Write-Host "  deleted $TaskName : $out"
    exit 0
}

if (-not (Test-Path $py)) { throw "python not found: $py" }
if (-not (Test-Path $quiet)) { throw "quiet wrapper not found: $quiet" }

# Same shape as the existing DSH_MM_* tasks: wscript + run-quiet.vbs + the .venv interpreter
# (no console window; also avoids the 261-char limit of `schtasks /TR`).
$tr = "wscript.exe //B //Nologo `"$quiet`" `"$py`" -m backend.services.rag_knowledge_service --index-cards"
if ($DryRun) {
    Write-Host "  [DryRun] schtasks /Create /TN $TaskName /TR $tr /SC DAILY /ST $Time /RU $env:USERNAME /F"
    exit 0
}
$out = schtasks /Create /TN $TaskName /TR $tr /SC DAILY /ST $Time /RU $env:USERNAME /F 2>&1
Write-Host "  $TaskName -> DAILY $Time : $out"

$x = schtasks /Query /TN $TaskName /XML 2>$null
foreach ($line in $x) {
    if ($line -match '<Command>(.*)</Command>') { Write-Host "  Command  : $($Matches[1])" }
    if ($line -match '<Arguments>(.*)</Arguments>') { Write-Host "  Arguments: $($Matches[1])" }
}
Write-Host '  verify: .venv\Scripts\python.exe -m backend.services.rag_knowledge_service --stats'
