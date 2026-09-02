# forensic_proc_watcher.ps1 — 进程生死取证 + 挂死现场自动 py-spy 抓栈
# 每 10s 记录 8000 端口属主；属主变化时对旧属主(僵尸)立即 py-spy dump。
# 日志: logs/forensic_proc_watch.log ; 栈: logs/hang_dump_auto_<ts>.txt
[CmdletBinding()]
param()

$LogFile = 'D:\001Alpha\Hyper-Alpha-Arena\logs\forensic_proc_watch.log'
$ErrorActionPreference = 'SilentlyContinue'
$script:LastOwner = -1

function Log([string]$msg) {
    $line = '{0} [forensic] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss.fff'), $msg
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

function Dump-Pid([int]$pid, [string]$why) {
    if ($pid -le 0) { return }
    $ts = Get-Date -Format 'yyyyMMdd_HHmmss'
    $path = "D:\001Alpha\Hyper-Alpha-Arena\logs\hang_dump_auto_${ts}_pid${pid}.txt"
    $out = & 'D:\001Alpha\Hyper-Alpha-Arena\.venv\Scripts\py-spy.exe' dump --pid $pid 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and $out) {
        Set-Content -Path $path -Value ("=== $why ts=" + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + " pid=$pid ===`n" + $out) -Encoding UTF8
        Log "py-spy dump saved: $path"
    } else {
        Log "py-spy dump failed for pid=$pid (rc=$LASTEXITCODE): $($out.Substring(0,[Math]::Min(120,$out.Length)))"
    }
}

Log 'watcher started (with auto py-spy dump on owner change)'

$timer = New-Object System.Timers.Timer
$timer.Interval = 10000
$timer.AutoReset = $true
$timerAction = {
    try {
        $conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
        if ($conns) {
            $pid8000 = ($conns | Select-Object -First 1).OwningProcess
            $proc = Get-Process -Id $pid8000 -ErrorAction SilentlyContinue
            $cpu = if ($proc) { [math]::Round($proc.CPU, 1) } else { -1 }
            $ws = if ($proc) { [math]::Round($proc.WorkingSet64 / 1MB) } else { -1 }
            if ($script:LastOwner -gt 0 -and $script:LastOwner -ne $pid8000) {
                Log "PORT8000 OWNER CHANGED: $($script:LastOwner) -> $pid8000"
                Dump-Pid $script:LastOwner "owner-changed-to-$pid8000"
            }
            $script:LastOwner = $pid8000
            Log "PORT8000 owner=$pid8000 cpu_s=$cpu ws_mb=$ws"
        } else {
            if ($script:LastOwner -gt 0) {
                Log "PORT8000 LOST (was $($script:LastOwner))"
                Dump-Pid $script:LastOwner "port-lost"
                $script:LastOwner = -1
            } else {
                Log 'PORT8000 no listener'
            }
        }
    } catch {
        Log "port snapshot error: $($_.Exception.Message)"
    }
}
Register-ObjectEvent -InputObject $timer -EventName Elapsed -Action $timerAction | Out-Null
$timer.Start()

# process stop events (python/cmd/powershell) with command line
$queryStop = "SELECT * FROM Win32_ProcessStopTrace"
$watcherStop = New-Object System.Management.ManagementEventWatcher($queryStop)
$queryStart = "SELECT * FROM Win32_ProcessStartTrace"
$watcherStart = New-Object System.Management.ManagementEventWatcher($queryStart)

$stopAction = {
    $e = $Event.SourceEventArgs.NewEvent
    $pn = $e.ProcessName
    if ($pn -match 'python|cmd|powershell') {
        try {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($e.ProcessID)"
            Log "STOP pid=$($e.ProcessID) name=$pn parent=$($p.ParentProcessId) cmd=$($p.CommandLine)"
        } catch {
            Log "STOP pid=$($e.ProcessID) name=$pn (cmd unavailable)"
        }
    }
}
$startAction = {
    $e = $Event.SourceEventArgs.NewEvent
    $pn = $e.ProcessName
    if ($pn -match 'python|cmd|powershell') {
        Start-Sleep -Milliseconds 400
        try {
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($e.ProcessID)"
            Log "START pid=$($e.ProcessID) name=$pn parent=$($p.ParentProcessId) cmd=$($p.CommandLine)"
        } catch {
            Log "START pid=$($e.ProcessID) name=$pn (cmd unavailable)"
        }
    }
}
Register-ObjectEvent -InputObject $watcherStop -EventName EventStopped -Action $stopAction | Out-Null
Register-ObjectEvent -InputObject $watcherStart -EventName EventStarted -Action $startAction | Out-Null
$watcherStop.Start()
$watcherStart.Start()
Log 'event subscriptions active'

try {
    while ($true) {
        Wait-Event -Timeout 60 | Out-Null
    }
} finally {
    $timer.Stop()
    $watcherStop.Stop()
    $watcherStart.Stop()
    Log 'watcher stopped'
}
