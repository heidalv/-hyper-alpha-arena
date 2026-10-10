# backend-death-trap.ps1 (2026-10-10 杀进程缉凶)
# 2 秒粒度的后端死亡陷阱：每次轮询记录
#   ① 8000 端口持有者的 pid + 父 pid + 启动时间
#   ② 全部 python/cmd/wscript/powershell 进程的 pid/ppid/启动时间/cmdline 摘要
# 后端 PID 变化时，把"死亡前最后一帧"完整落盘（logs/backend-death-trap.log），
# 凶手进程（如果它是脚本/进程）一定会在那一帧里现形。
$ErrorActionPreference = 'SilentlyContinue'
$Log = 'D:\001Alpha\Hyper-Alpha-Arena\logs\backend-death-trap.log'

function Get-PyCensus {
    $rows = @()
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='cmd.exe' OR Name='wscript.exe' OR Name='powershell.exe' OR Name='pwsh.exe' OR Name='conhost.exe'" |
        ForEach-Object {
            $cl = $_.CommandLine
            if ($cl) { if ($cl.Length -gt 110) { $cl = $cl.Substring(0,110) } } else { $cl = '' }
            $mb = 0.0
            $wp = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
            if ($wp) { $mb = [math]::Round($wp.WorkingSet64 / 1MB, 0) }
            $rows += [PSCustomObject]@{
                pid=$_.ProcessId; ppid=$_.ParentProcessId
                started=$_.CreationDate.ToString('HH:mm:ss')
                name=$_.Name; cl=$cl; mb=$mb
            }
        }
    return $rows
}

function Get-PortOwner {
    $c = Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object -First 1
    if (-not $c) { return 0 }
    return $c.OwningProcess
}

$prevOwner = 0
$prevCensus = @()
while ($true) {
    Start-Sleep -Seconds 2
    $owner = Get-PortOwner
    if ($owner -ne $prevOwner) {
        $ts = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
        $line = "[$ts] BACKEND_CHANGE $prevOwner -> $owner"
        Add-Content -Path $Log -Value $line -Encoding UTF8
        if ($prevOwner -ne 0 -and $owner -eq 0) {
            # 死亡：落盘死亡前一帧的全部进程（2s 内变化）
            Add-Content -Path $Log -Value '  --- 死亡前一帧进程表 ---' -Encoding UTF8
            $prevCensus | Sort-Object started, pid | ForEach-Object {
                Add-Content -Path $Log -Value ('    {0} pid={1} ppid={2} mem={3}MB {4} {5}' -f $_.started, $_.pid, $_.ppid, $_.mb, $_.name, $_.cl) -Encoding UTF8
            }
            Add-Content -Path $Log -Value '  --- 死亡后一帧进程表 ---' -Encoding UTF8
            Get-PyCensus | Sort-Object started, pid | ForEach-Object {
                Add-Content -Path $Log -Value ('    {0} pid={1} ppid={2} mem={3}MB {4} {5}' -f $_.started, $_.pid, $_.ppid, $_.mb, $_.name, $_.cl) -Encoding UTF8
            }
        }
        $prevOwner = $owner
    }
    $prevCensus = Get-PyCensus
}
