$b = 'http://127.0.0.1:8000/api/trading'
$tally = @{}
$quoting = 0
for ($i = 0; $i -lt 6; $i++) {
  $r = Invoke-RestMethod "$b/lanes/mm_asterdex/shadow/tick" -Method Post -TimeoutSec 30
  $quoting += $r.quoting_symbols
  foreach ($d in $r.decisions) {
    $k = if ([string]::IsNullOrEmpty($d.skip)) { 'OK' } else { $d.skip }
    if ($tally.ContainsKey($k)) { $tally[$k]++ } else { $tally[$k] = 1 }
  }
  Start-Sleep -Seconds 2
}
Write-Output "6 个 tick 平均挂单数: $([math]::Round($quoting / 6, 2))/6"
Write-Output '--- skip 原因分布（6 tick × 6 币 = 36 次判定）---'
$tally.GetEnumerator() | Sort-Object Value -Descending | ForEach-Object { "  {0,-24} {1}" -f $_.Key, $_.Value }
