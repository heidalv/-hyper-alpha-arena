# =====================================================================
# cleanup.ps1 — 仓库/工作区一次性文件安全清理
#
# 设计原则：
#   1. 默认 dry-run：只列出将要处理的文件与体积，不碰任何东西。
#   2. -Execute 时【移动】到 _cleanup_archive\<时间戳>\（同盘秒移、可回滚），
#      而不是直接删除。
#   3. 绝不触碰：git 仓库内容、.venv*/node_modules、后端源码、docs、
#      被运行中进程占用的文件（移动失败自动跳过并报告）。
#
# 用法：
#   powershell -File scripts\cleanup.ps1              # 只看报告
#   powershell -File scripts\cleanup.ps1 -Execute     # 执行移动
#   powershell -File scripts\cleanup.ps1 -Execute -IncludeWorkspace   # 含工作区根目录
#   powershell -File scripts\cleanup.ps1 -Execute -DeleteCaches       # 额外删缓存目录
# =====================================================================
[CmdletBinding()]
param(
    [string]$RepoRoot = "D:\001Alpha\Hyper-Alpha-Arena",
    [string]$WorkspaceRoot = "D:\001Alpha",
    [switch]$Execute,
    [switch]$IncludeWorkspace,
    [switch]$DeleteCaches
)

$ErrorActionPreference = "SilentlyContinue"
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
$ArchiveRoot = Join-Path $RepoRoot "_cleanup_archive\$ts"

# ---------- 规则：要处理的模式（相对根目录的匹配列表） ----------
# 每条：@{ Root = <路径>; Patterns = @(<通配>); ExcludeDirs = @(<永不触碰的目录名>) }
$RepoJunkPatterns = @(
    "temp_*.py", "temp_*.txt", "temp_*.md",
    "audit_*.py", "audit_*.txt",
    "tmp_*.py", "tmp_*.txt", "tmp_*.bat",
    "_tmp_*", "_backfill_settle.py",
    "check_tiers.py",
    "test_*.py", "test_*.png", "test_*.db*",
    "TEST_NOW.md",
    "pytest_full_run.txt", "pytest_last_run.txt",
    "backend_restart*.log", "restart_backend*.log",
    ".env.bak.*"
)
$RepoExcludeDirs = @("backend", "frontend", "frontend-next", "desktop", "mobile",
                     "docs", "scripts", "tests", "node_modules", ".venv", ".venv313",
                     "venv-gpu", ".git", "data", "logs", "postgres_backup",
                     "qaa_chromadb", "qaa_knowledge", "qaa_memory", "qaa_workflow",
                     "obsidian_vault", "releases", "deploy", "config", "image",
                     "models", "training", "tools", "reports", "screenshots",
                     ".qoder", ".cursor", ".claude", ".opencode", ".run",
                     ".github", ".pytest_cache", "cloud_factor_library",
                     "_cleanup_archive")

$WsJunkPatterns = @(
    "_tmp_*.py", "_tmp_*.ps1",
    "backend_restart*.log", "restart_backend*.log",
    "audit_runtime_evidence.py",
    "temp_*.py", "temp_*.txt", "temp_recalc_*.py", "temp_verify_*.py",
    "test_shot.png", "test.db*", "query"
)
$WsJunkDirs = @("pg_index_backup_20260809", "pg_wal_backup_20260809", "_verify_shots")
$WsExcludeDirs = @("Hyper-Alpha-Arena", "data", ".venv", "node_modules", ".git")

$cacheDirs = @(
    (Join-Path $WorkspaceRoot ".pip_cache"),
    (Join-Path $WorkspaceRoot ".pytest_cache")
)

# ---------- 收集候选 ----------
$candidates = New-Object System.Collections.Generic.List[object]

foreach ($p in $RepoJunkPatterns) {
    Get-ChildItem -Path $RepoRoot -File -Filter $p |
        Where-Object { $_.FullName -notmatch "\\.git\\" } |
        ForEach-Object { $candidates.Add([pscustomobject]@{
            Path = $_.FullName; Size = $_.Length; Kind = "repo-file"; Pattern = $p }) }
}

if ($IncludeWorkspace) {
    foreach ($p in $WsJunkPatterns) {
        Get-ChildItem -Path $WorkspaceRoot -File -Filter $p |
            Where-Object { $_.FullName -notmatch "\\Hyper-Alpha-Arena\\" -and $_.FullName -notmatch "\\.git\\" } |
            ForEach-Object { $candidates.Add([pscustomobject]@{
                Path = $_.FullName; Size = $_.Length; Kind = "ws-file"; Pattern = $p }) }
    }
    foreach ($d in $WsJunkDirs) {
        $dir = Join-Path $WorkspaceRoot $d
        if (Test-Path $dir) {
            $sz = (Get-ChildItem $dir -Recurse -File | Measure-Object Length -Sum).Sum
            $candidates.Add([pscustomobject]@{
                Path = $dir; Size = $sz; Kind = "ws-dir"; Pattern = $d })
        }
    }
}

# ---------- 输出 ----------
$total = ($candidates | Measure-Object Size -Sum).Sum
$fmt = { param($b) if ($b -gt 1MB) { "{0:N1} MB" -f ($b/1MB) } elseif ($b -gt 1KB) { "{0:N0} KB" -f ($b/1KB) } else { "$b B" } }

Write-Output ""
Write-Output ("==== 清理预览（{0} 项 / 共 {1}）====" -f $candidates.Count, (& $fmt $total))
if ($candidates.Count -eq 0) { Write-Output "没有需要清理的文件。"; exit 0 }

$candidates | Sort-Object Size -Descending | Select-Object -First 40 |
    ForEach-Object { "{0,10}  {1}  [{2}]" -f (& $fmt $_.Size), $_.Path, $_.Kind }

if (-not $Execute) {
    Write-Output ""
    Write-Output "【dry-run】未做任何修改。确认后加 -Execute 执行移动。"
    exit 0
}

# ---------- 执行：移动到归档（可回滚） ----------
$moved = 0; $movedBytes = 0L; $failed = 0
foreach ($c in $candidates) {
    $rel = ""
    if ($c.Kind -like "repo-*") { $rel = "repo\" + $c.Path.Substring($RepoRoot.Length).TrimStart("\") }
    else { $rel = "ws\" + $c.Path.Substring($WorkspaceRoot.Length).TrimStart("\") }
    $dest = Join-Path $ArchiveRoot $rel
    $destDir = Split-Path $dest -Parent
    New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    try {
        Move-Item -LiteralPath $c.Path -Destination $dest -Force -ErrorAction Stop
        $moved++; $movedBytes += $c.Size
    } catch {
        $failed++
        Write-Warning ("跳过（被占用或无权限）: " + $c.Path)
    }
}

# 缓存目录：直接删除（可再生，归档无意义），需 -DeleteCaches
$deletedCaches = 0
if ($DeleteCaches) {
    foreach ($d in $cacheDirs) {
        if (Test-Path $d) {
            Remove-Item -LiteralPath $d -Recurse -Force
            if (-not (Test-Path $d)) { $deletedCaches++ }
        }
    }
}

Write-Output ""
Write-Output ("==== 完成 ====")
Write-Output ("移动 {0} 项（{1}）→ {2}" -f $moved, (& $fmt $movedBytes), $ArchiveRoot)
if ($failed -gt 0) { Write-Output ("跳过 {0} 项（通常是被运行中的后端占用，可停服后重跑）" -f $failed) }
if ($DeleteCaches) { Write-Output ("删除缓存目录 {0} 个" -f $deletedCaches) }
Write-Output ""
Write-Output "回滚方法：把 _cleanup_archive\<时间戳>\ 下的内容移回原处即可。"
Write-Output "git 已跟踪过的文件也可用 git checkout <commit> -- <path> 找回。"
