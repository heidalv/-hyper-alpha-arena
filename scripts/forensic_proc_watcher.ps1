# forensic_proc_watcher.ps1 — 8000 端口属主轮询 + 挂死自动 py-spy 抓栈（单线程轮询版，无 WMI 事件复杂度）
# 每 10s 记录属主 pid/cpu/ws；属主变化或端口丢失时立即对旧属主 dump 全线程栈。
# 日志: logs/forensic_proc_watch.log ; 栈: logs/hang_dump_auto_<ts>_pid<pid>.txt
[CmdletBinding()]
param()

$ErrorActionPreference = 'SilentlyContinue'
$LogFile = 'D:\001Alpha\Hyper-Alpha-Arena\logs\forensic_proc_watch.log'
$PySpy = 'D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\py-spy.exe'
$LastOwner = -1

function Log([string]$msg) {
    $line = '{0} [forensic] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Dump-Pid([int]$PidToDump, [string]$Why) {
    if ($PidToDump -le 0) { return }
    $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
    $path = "D:\001Alpha\Hyper-Alpha-Arena\logs\hang_dump_auto_${ts}_pid${PidToDump}.txt"
    $out = & $PySpy dump --pid $PidToDump 2>&1 | Out-String
    if ($out -and $out -match 'Thread') {
        Set-Content -Path $path -Value ("=== $Why ts=" + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " pid=$PidToDump ===`n" + $out) -Encoding UTF8
        Log "py-spy dump saved: $path"
    } else {
        $short = if ($out) { $out.Substring(0, [Math]::Min(120, $out.Length)) } else { '' }
        Log "py-spy dump failed pid=$PidToDump rc=${LASTEXITCODE}: $short"
    }
}

Log 'watcher started (poll loop, auto py-spy dump on owner change)'

while ($true) {
    try {
        $conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if ($conns) {
            $pid8000 = ($conns | Select-Object -First 1).OwningProcess
            if ($LastOwner -gt 0 -and $LastOwner -ne $pid8000) {
                Log "PORT8000 OWNER CHANGED: $LastOwner -> $pid8000"
                Dump-Pid $LastOwner "owner-changed-to-$pid8000"
            }
            $LastOwner = $pid8000
            $proc = Get-Process -Id $pid8000 -ErrorAction SilentlyContinue
            $cpu = if ($proc) { [math]::Round($proc.CPU, 1) } else { -1 }
            $ws = if ($proc) { [math]::Round($proc.WorkingSet64 / 1MB) } else { -1 }
            Log "PORT8000 owner=$pid8000 cpu_s=$cpu ws_mb=$ws"
        } else {
            if ($LastOwner -gt 0) {
                Log "PORT8000 LOST (was $LastOwner)"
                Dump-Pid $LastOwner 'port-lost'
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
