# =====================================================================
# db_backup.ps1 — PostgreSQL 4 库每日备份 + 保留策略（R2）
#
# 设计（整改设计文档 R2）：
#   - 从仓库 .env 解析 4 个 DATABASE_URL（密码只在子进程环境变量中传递，
#     绝不写入日志/输出）
#   - pg_dump -Fc（自定义格式，pg_restore 可恢复）→ postgres_backup\<日期>\<库名>.dump
#   - 校验：退出码 0 且 dump 文件 > 1MB，否则记失败
#   - 清理 > 7 天的备份目录；每周日追加 pg_dumpall --globals-only（角色/权限）
#   - 摘要写入 logs/db_backup.log（追加式）
#
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\db_backup.ps1
#   powershell ... -RetentionDays 14      # 自定义保留天数
#   powershell ... -NoCleanup             # 只备份不清理
# 任务计划（每日 02:00）：
#   schtasks /Create /TN "AlphaArena-DailyDBBackup" /SC DAILY /ST 02:00 \
#     /TR "powershell -NoProfile -ExecutionPolicy Bypass -File D:\001Alpha\Hyper-Alpha-Arena\scripts\db_backup.ps1"
# =====================================================================
[CmdletBinding()]
param(
    [string]$RepoRoot = "D:\001Alpha\Hyper-Alpha-Arena",
    [int]$RetentionDays = 7,
    [int]$LockWaitMs = 60000,
    [switch]$NoCleanup
)

$ErrorActionPreference = 'Stop'
$ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$today = Get-Date -Format 'yyyyMMdd'

$LogDir = Join-Path $RepoRoot 'logs'
$BackupRoot = Join-Path $RepoRoot 'postgres_backup'
$LogFile = Join-Path $LogDir 'db_backup.log'
New-Item -ItemType Directory -Path $LogDir, $BackupRoot -Force | Out-Null

function Write-Log([string]$msg) {
    $line = "$ts $msg"
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

# ---------- 定位 pg_dump / pg_dumpall ----------
$pgBinCandidates = @(
    "C:\Program Files\PostgreSQL\15\bin",
    "C:\Program Files\PostgreSQL\16\bin",
    "C:\Program Files\PostgreSQL\17\bin"
)
$pgBin = $null
foreach ($c in $pgBinCandidates) {
    if (Test-Path (Join-Path $c 'pg_dump.exe')) { $pgBin = $c; break }
}
if (-not $pgBin) {
    $cmd = Get-Command pg_dump -ErrorAction SilentlyContinue
    if ($cmd) { $pgBin = Split-Path $cmd.Source } else {
        Write-Log "[FATAL] pg_dump not found. Install PostgreSQL or add to PATH."
        exit 1
    }
}
$pgDump = Join-Path $pgBin 'pg_dump.exe'
$pgDumpAll = Join-Path $pgBin 'pg_dumpall.exe'

# ---------- 解析 .env 中的 4 个数据库 URL ----------
$envFile = Join-Path $RepoRoot '.env'
if (-not (Test-Path $envFile)) { Write-Log "[FATAL] .env not found"; exit 1 }

$envMap = @{}
Get-Content $envFile | ForEach-Object {
    if ($_ -match '^([A-Z][A-Z0-9_]*)=(.*)$') { $envMap[$Matches[1]] = $Matches[2] }
}

$dbKeys = @(
    @{ Key = 'DATABASE_URL';           Name = 'alpha_arena' },
    @{ Key = 'MARKET_DATABASE_URL';    Name = 'alpha_market' },
    @{ Key = 'ANALYTICS_DATABASE_URL'; Name = 'alpha_analytics' },
    @{ Key = 'SNAPSHOT_DATABASE_URL';  Name = 'alpha_snapshots' }
)

$targets = @()
foreach ($d in $dbKeys) {
    $url = $envMap[$d.Key]
    if (-not $url) { Write-Log "[WARN] $($d.Key) 未配置，跳过 $($d.Name)"; continue }
    if ($url -match '^postgresql(\+\w+)?://([^:]+):([^@]+)@([^:/]+):?(\d+)?/([A-Za-z0-9_]+)$') {
        $targets += [pscustomobject]@{
            Name = $d.Name
            User = $Matches[2]
            Pass = $Matches[3]
            Host = $Matches[4]
            Port = if ($Matches[5]) { $Matches[5] } else { '5432' }
            Db   = $Matches[6]
        }
    } else {
        Write-Log "[WARN] $($d.Key) 解析失败（非标准 postgresql URL），跳过 $($d.Name)"
    }
}
Write-Log "[INFO] 待备份 $($targets.Count) 个库"

# ---------- 执行备份 ----------
$outDir = Join-Path $BackupRoot $today
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$failures = 0
foreach ($t in $targets) {
    $dumpPath = Join-Path $outDir "$($t.Name).dump"
    $env:PGPASSWORD = $t.Pass
    try {
        # pg_dump 失败信息走 stderr；EAP=Stop 会把 native stderr 当终止错误，
        # 此处临时放行以便捕获并写日志
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        $errOut = & $pgDump -h $t.Host -p $t.Port -U $t.User -d $t.Db -Fc --lock-wait-timeout=$LockWaitMs -f $dumpPath 2>&1 | Out-String
        $code = $LASTEXITCODE
        $ErrorActionPreference = $prevEap
    } finally {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    if ($code -eq 0 -and (Test-Path $dumpPath)) {
        $sz = (Get-Item $dumpPath).Length
        if ($sz -gt 1MB) {
            Write-Log "[OK] $($t.Name) -> $dumpPath ($([math]::Round($sz/1MB,1)) MB)"
        } else {
            $failures++
            Write-Log "[FAIL] $($t.Name) dump 过小 ($sz bytes)，疑似空库/失败"
        }
    } else {
        $failures++
        $firstErr = ($errOut -split "`n" | Where-Object { $_.Trim() } | Select-Object -First 1)
        Write-Log "[FAIL] $($t.Name) pg_dump exit=$code : $firstErr"
        if ($firstErr -match 'row-level security|行级安全|RLS') {
            Write-Log "[HINT] 备份账号缺少 BYPASSRLS：需超级用户执行 ALTER ROLE $($t.User) BYPASSRLS; 或改用专用备份角色（见 docs/ops/数据库备份与恢复.md）"
        }
    }
}

# ---------- 周日：globals 备份 ----------
if ((Get-Date).DayOfWeek -eq 'Sunday' -and (Test-Path $pgDumpAll)) {
    $globalsDir = Join-Path $BackupRoot 'globals'
    New-Item -ItemType Directory -Path $globalsDir -Force | Out-Null
    $gPath = Join-Path $globalsDir "globals_$today.sql"
    $env:PGPASSWORD = ($targets | Select-Object -First 1).Pass
    try { & $pgDumpAll -h ($targets | Select-Object -First 1).Host --globals-only -f $gPath 2>&1 | Out-Null; $gCode = $LASTEXITCODE }
    finally { Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue }
    if ($gCode -eq 0) { Write-Log "[OK] globals -> $gPath" } else { Write-Log "[WARN] pg_dumpall globals exit=$gCode" }
}

# ---------- 清理 > RetentionDays ----------
if (-not $NoCleanup) {
    Get-ChildItem $BackupRoot -Directory | Where-Object { $_.Name -match '^\d{8}$' } | ForEach-Object {
        try {
            $d = [datetime]::ParseExact($_.Name, 'yyyyMMdd', $null)
            if ((Get-Date) - $d -gt [timespan]::FromDays($RetentionDays)) {
                Remove-Item $_.FullName -Recurse -Force
                Write-Log "[CLEAN] 删除过期备份目录 $($_.Name) (>$RetentionDays 天)"
            }
        } catch { Write-Log "[WARN] 目录名解析失败: $($_.Name)" }
    }
    Get-ChildItem (Join-Path $BackupRoot 'globals') -File -ErrorAction SilentlyContinue |
        Where-Object { (Get-Date) - $_.LastWriteTime -gt [timespan]::FromDays($RetentionDays) } |
        ForEach-Object { Remove-Item $_.FullName -Force; Write-Log "[CLEAN] 删除过期 globals $($_.Name)" }
}

# ---------- 摘要 ----------
if ($failures -eq 0) {
    Write-Log "[SUMMARY] backup OK: $($targets.Count) dbs -> $outDir"
    exit 0
} else {
    Write-Log "[SUMMARY] backup FAILED: $failures 个库失败（检查上方 FAIL 行；飞书告警接入见 R6）"
    exit 1
}
