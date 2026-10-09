# [h771] Focused probe: catch the ~1/min console spawner with FULL parent + grandparent chains.
# ASCII only.
$known = @{}
Get-CimInstance Win32_Process -Filter "Name='conhost.exe'" | ForEach-Object { $known[$_.ProcessId] = $true }
$hits = @()
$t0 = Get-Date
while (((Get-Date) - $t0).TotalSeconds -lt 260) {
    Get-CimInstance Win32_Process -Filter "Name='conhost.exe'" | ForEach-Object {
        if (-not $known.ContainsKey($_.ProcessId)) {
            $known[$_.ProcessId] = $true
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
            $line = (Get-Date -Format 'HH:mm:ss') + ' | '
            if ($p) {
                $line += 'P=' + $p.Name + '[' + $p.ProcessId + '] ' + $p.CommandLine
                $g = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
                if ($g) { $line += ' || GP=' + $g.Name + '[' + $g.ProcessId + '] ' + $g.CommandLine }
                else { $line += ' || GP=(exited)' }
            }
            else { $line += 'P=(exited)' }
            $hits += $line
        }
    }
    Start-Sleep -Seconds 1
}
Write-Host ('total new conhost in 260s: ' + $hits.Count)
$i = 0
foreach ($h in $hits) {
    $i++
    $short = $h
    if ($short.Length -gt 260) { $short = $short.Substring(0, 260) }
    Write-Host ('--- hit ' + $i + ': ' + $short)
}
