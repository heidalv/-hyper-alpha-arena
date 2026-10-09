# worker-watchdog.ps1 - mm_lane_worker liveness watchdog (scheduled every 5 min)
# ASCII-only by design: Windows PowerShell 5.1 reads .ps1 as ANSI without BOM.
$log = "D:\001Alpha\Hyper-Alpha-Arena\logs\worker_watchdog.log"
$procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
          Where-Object { $_.CommandLine -match 'mm_lane_worker' })
if ($procs.Count -eq 0) {
    $r = schtasks /Run /TN DSH_MM_WORKER 2>&1
    $msg = "{0} worker offline -> restart rc={1} {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $LASTEXITCODE, ($r -join ' ')
    Add-Content -Path $log -Encoding UTF8 -Value $msg
} else {
    if ((Get-Random -Maximum 60) -eq 0) {
        $msg = "{0} worker alive ({1} procs)" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $procs.Count
        Add-Content -Path $log -Encoding UTF8 -Value $msg
    }
}
