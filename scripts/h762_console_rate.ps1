# [h762] 统计控制台创建速率 + 归因(供"黑框弹出"排查)
$known = @{}
Get-CimInstance Win32_Process -Filter "Name='conhost.exe'" | ForEach-Object { $known[$_.ProcessId] = $true }
$agg = @{}
$t0 = Get-Date
while (((Get-Date) - $t0).TotalSeconds -lt 120) {
    Get-CimInstance Win32_Process -Filter "Name='conhost.exe'" | ForEach-Object {
        if (-not $known.ContainsKey($_.ProcessId)) {
            $known[$_.ProcessId] = $true
            $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($_.ParentProcessId)" -ErrorAction SilentlyContinue
            $key = 'parent-exited'
            if ($p) { $key = $p.Name + ' :: ' + $p.CommandLine }
            if ($key.Length -gt 130) { $key = $key.Substring(0, 130) }
            if (-not $agg.ContainsKey($key)) { $agg[$key] = 0 }
            $agg[$key]++
        }
    }
    Start-Sleep -Seconds 2
}
$total = ($agg.Values | Measure-Object -Sum).Sum
Write-Host "120 秒内新建控制台: $total 个(约 $([math]::Round($total/2,1))/分钟)"
$agg.GetEnumerator() | Sort-Object -Property Value -Descending | ForEach-Object {
    Write-Host ("  {0,3}x  {1}" -f $_.Value, $_.Key)
}
