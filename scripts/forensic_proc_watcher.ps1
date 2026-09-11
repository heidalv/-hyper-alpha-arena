# forensic_proc_watcher.ps1 — 8000 端口属主轮询 + 挂死自动 py-spy 抓栈 + 60s 快照轮转
# - 每 10s 记录属主 pid/cpu/ws；属主变化或端口丢失立即对旧属主 dump 全线程栈。
# - 每 60s 例行 py-spy 快照（轮转 5 份），进程瞬时消失时也有 <=60s 前的现场。
# 日志: logs/forensic_proc_watch.log ; 现场: logs/hang_dump_auto_*.txt / hang_snap_r*.txt
[CmdletBinding()]
param()

$ErrorActionPreference = 'SilentlyContinue'
$LogFile = 'D:\001Alpha\Hyper-Alpha-Arena\logs\forensic_proc_watch.log'
$PySpy = 'D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\py-spy.exe'
$LastOwner = -1
$SnapIdx = 0

function Log([string]$msg) {
    $line = '{0} [forensic] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Invoke-Dump([int]$PidToDump, [string]$Path, [string]$Why) {
    $out = & $PySpy dump --pid $PidToDump 2>&1 | Out-String
    if ($out -and $out -match 'Thread') {
        Set-Content -Path $Path -Value ("=== $Why ts=" + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " pid=$PidToDump ===`n" + $out) -Encoding UTF8
        return $true
    }
    return $false
}

Log 'watcher started (poll loop + 60s snapshot ring)'

$Tick = 0
while ($true) {
    try {
        $Tick++
        $conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if ($conns) {
            $pid8000 = ($conns | Select-Object -First 1).OwningProcess
            if ($LastOwner -gt 0 -and $LastOwner -ne $pid8000) {
                Log "PORT8000 OWNER CHANGED: $LastOwner -> $pid8000"
                $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
                $path = "D:\001Alpha\Hyper-Alpha-Arena\logs\hang_dump_auto_${ts}_pid${LastOwner}.txt"
                if (Invoke-Dump $LastOwner $path "owner-changed-to-$pid8000") {
                    Log "py-spy dump saved: $path"
                } else {
                    Log "py-spy dump failed pid=$LastOwner (already gone)"
                }
            }
            $LastOwner = $pid8000
            $proc = Get-Process -Id $pid8000 -ErrorAction SilentlyContinue
            $cpu = if ($proc) { [math]::Round($proc.CPU, 1) } else { -1 }
            $ws = if ($proc) { [math]::Round($proc.WorkingSet64 / 1MB) } else { -1 }
            Log "PORT8000 owner=$pid8000 cpu_s=$cpu ws_mb=$ws"
            # 60s 快照轮转
            if (($Tick % 6) -eq 0) {
                $snap = "D:\001Alpha\Hyper-Alpha-Arena\logs\hang_snap_r$SnapIdx.txt"
                if (Invoke-Dump $pid8000 $snap ("snap-" + $SnapIdx)) {
                    Log "snapshot saved: hang_snap_r$SnapIdx.txt"
                } else {
                    Log "snapshot dump failed pid=$pid8000"
                }
                $SnapIdx = ($SnapIdx + 1) % 5
            }
        } else {
            if ($LastOwner -gt 0) {
                Log "PORT8000 LOST (was $LastOwner)"
                $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
                $path = "D:\001Alpha\Hyper-Alpha-Arena\logs\hang_dump_auto_${ts}_pid${LastOwner}.txt"
                if (Invoke-Dump $LastOwner $path 'port-lost') {
                    Log "py-spy dump saved: $path"
                } else {
                    Log "py-spy dump failed pid=$LastOwner (already gone)"
                }
                # 保留死亡前快照：复制轮转快照到 death 目录
                $deathDir = "D:\001Alpha\Hyper-Alpha-Arena\logs\death_${ts}_pid${LastOwner}"
                try {
                    New-Item -ItemType Directory -Force -Path $deathDir | Out-Null
                    Copy-Item 'D:\001Alpha\Hyper-Alpha-Arena\logs\hang_snap_r*.txt' -Destination $deathDir -Force -ErrorAction SilentlyContinue
                    Log "snapshot ring preserved to $deathDir"
                } catch {
                    Log "preserve ring failed: $($_.Exception.Message)"
                }
                $LastOwner = -1
            } else {
                Log 'PORT8000 no listener'
            }
        }
    } catch {
        Log "poll error: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 10
}
