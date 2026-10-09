# [h770] 6-minute black-box probe: count new conhosts + newly appeared visible windows.
# ASCII only (PowerShell 5.1 mis-parses UTF-8 without BOM).
$known = @{}
Get-CimInstance Win32_Process -Filter "Name='conhost.exe'" | ForEach-Object { $known[$_.ProcessId] = $true }
$titles = @{}
Get-Process | Where-Object { $_.MainWindowTitle -ne '' } | ForEach-Object { $titles[$_.Id] = $true }
$agg = @{}
$visible = @()
$t0 = Get-Date
while (((Get-Date) - $t0).TotalSeconds -lt 360) {
    Get-CimInstance Win32_Process -Filter "Name='conhost.exe'" | ForEach-Object {
        if (-not $known.ContainsKey($_.ProcessId)) {
            $known[$_.ProcessId] = $true
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
            $key = 'parent-exited'
            if ($p) { $key = $p.Name + ' :: ' + $p.CommandLine }
            if ($key.Length -gt 110) { $key = $key.Substring(0, 110) }
            if (-not $agg.ContainsKey($key)) { $agg[$key] = 0 }
            $agg[$key]++
        }
    }
    Get-Process | Where-Object { $_.MainWindowTitle -ne '' -and -not $titles.ContainsKey($_.Id) } | ForEach-Object {
        $line = $_.ProcessName + ' pid=' + $_.Id + " title='" + $_.MainWindowTitle + "'"
        $visible += $line
        $titles[$_.Id] = $true
    }
    Start-Sleep -Seconds 2
}
$tot = ($agg.Values | Measure-Object -Sum).Sum
Write-Host '=== 6-minute probe ==='
Write-Host ('new conhost: {0}  (~{1}/min)' -f $tot, [math]::Round($tot / 6.0, 1))
$agg.GetEnumerator() | Sort-Object -Property Value -Descending | ForEach-Object {
    Write-Host ('  {0,3}x {1}' -f $_.Value, $_.Key)
}
Write-Host ('newly visible windows: {0}' -f $visible.Count)
$visible | Select-Object -First 10 | ForEach-Object { Write-Host ('  ' + $_) }
