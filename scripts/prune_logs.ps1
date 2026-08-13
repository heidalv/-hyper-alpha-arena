# =====================================================================
# prune_logs.ps1 — 日志轮转/存量清理（R6-1）
#
# 设计（整改设计文档 R6-1）：
#   - 删除 logs/ 下超过 RetentionDays 天的文件（排除 *.pid、当天文件）
#   - 默认 dry-run 只报告；-Execute 移动（可回滚）到 _cleanup_archive\<ts>\logs\
#   - 主后端日志已由 main.py:_bootstrap_logging 的 RotatingFileHandler 轮转，
#     本脚本负责其余写入点（前端/数据中心/重启日志等）的存量治理。
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\prune_logs.ps1            # 预览
#   powershell ... -Execute -RetentionDays 7                                              # 执行
#   cleanup.ps1 -PruneLogs 亦会调用本逻辑
# =====================================================================
[CmdletBinding()]
param(
    [string]$RepoRoot = "D:\001Alpha\Hyper-Alpha-Arena",
    [int]$RetentionDays = 7,
    [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$LogDir = Join-Path $RepoRoot 'logs'
$ts = Get-Date -Format 'yyyyMMdd_HHmmss'
$ArchiveRoot = Join-Path $RepoRoot "_cleanup_archive\$ts\logs"

if (-not (Test-Path $LogDir)) { Write-Host "[info] logs/ 不存在"; exit 0 }

$cutoff = (Get-Date).AddDays(-$RetentionDays)
$todayStr = (Get-Date).ToString('yyyy-MM-dd')

$targets = Get-ChildItem $LogDir -File |
    Where-Object {
        $_.Extension -ne '.pid' -and
        $_.LastWriteTime -lt $cutoff -and
        $_.Name -notmatch '^_'            # 跳过临时诊断文件（_*.txt 等）
    }

$total = ($targets | Measure-Object Length -Sum).Sum
Write-Host ("候选: {0} 个文件 / {1:N1} MB（> {2} 天，排除 .pid 与 _ 前缀）" -f $targets.Count, ($total / 1MB), $RetentionDays)

if (-not $Execute) {
    $targets | Sort-Object Length -Descending | Select-Object -First 15 |
        ForEach-Object { Write-Host ("  {0,8:N1} MB  {1}  {2}" -f ($_.Length / 1MB), $_.LastWriteTime.ToString('yyyy-MM-dd'), $_.Name) }
    Write-Host "[dry-run] 未做修改。确认后加 -Execute。"
    exit 0
}

New-Item -ItemType Directory -Path $ArchiveRoot -Force | Out-Null
$moved = 0; $movedBytes = 0L; $failed = 0
foreach ($f in $targets) {
    try {
        Move-Item -LiteralPath $f.FullName -Destination (Join-Path $ArchiveRoot $f.Name) -Force -ErrorAction Stop
        $moved++; $movedBytes += $f.Length
    } catch {
        $failed++
        Write-Warning ("跳过（被占用）: " + $f.Name)
    }
}
Write-Host ("完成: 移动 {0} 个 / {1:N1} MB -> {2}（跳过 {3} 个被占用）" -f $moved, ($movedBytes / 1MB), $ArchiveRoot, $failed)
Write-Host "回滚：把归档目录内文件移回 logs/ 即可。"
