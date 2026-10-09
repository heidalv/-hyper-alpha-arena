# [h767] Accelerate all learning/evaluation task cadences (test phase).
# Rebuild each task with the same TR but a faster schedule.
$ErrorActionPreference = 'Continue'
$map = @(
    @{ Name = 'DSH_MM_SELF_TUNE';         Mode = 'HOURLY'; N = 1  },
    @{ Name = 'DSH_MM_SELF_TUNE_VERDICT'; Mode = 'MINUTE'; N = 15 },
    @{ Name = 'DSH_MM_GATE_AUDIT';        Mode = 'MINUTE'; N = 30 },
    @{ Name = 'DSH_MM_SURFACE';           Mode = 'HOURLY'; N = 1  },
    @{ Name = 'DSH_MM_POSTERIOR';         Mode = 'HOURLY'; N = 2  },
    @{ Name = 'DSH_MM_BANDIT';            Mode = 'HOURLY'; N = 1  },
    @{ Name = 'DSH_MM_BANDIT_COMPARE';    Mode = 'HOURLY'; N = 2  },
    @{ Name = 'DSH_MM_MARKOUT_KPI';       Mode = 'MINUTE'; N = 10 },
    @{ Name = 'DSH_MM_FILL_AUDIT';        Mode = 'MINUTE'; N = 10 },
    @{ Name = 'DSH_MM_VOL_SCREEN';        Mode = 'MINUTE'; N = 5  },
    @{ Name = 'DSH_MM_SYMBOL_SYNC';       Mode = 'MINUTE'; N = 5  }
)
foreach ($t in $map) {
    $x = schtasks /Query /TN $t.Name /XML 2>$null
    if (-not $x) { Write-Host ("  MISSING {0}" -f $t.Name); continue }
    $cmd = ''
    $arg = ''
    foreach ($line in $x) {
        if ($line -match '<Command>(.*)</Command>') { $cmd = $Matches[1] }
        if ($line -match '<Arguments>(.*)</Arguments>') { $arg = $Matches[1] }
    }
    if (-not $cmd) { Write-Host ("  NO-COMMAND {0}" -f $t.Name); continue }
    $cmd = $cmd.Replace('&quot;', '"').Replace('&amp;', '&').Replace('&lt;', '<').Replace('&gt;', '>')
    $arg = $arg.Replace('&quot;', '"').Replace('&amp;', '&').Replace('&lt;', '<').Replace('&gt;', '>')
    $tr = $cmd
    if ($arg) { $tr = "$cmd $arg" }
    $out = schtasks /Create /TN $t.Name /TR $tr /SC $t.Mode /MO $t.N /RU heida /F 2>&1
    Write-Host ("  {0,-28} -> {1} {2}  {3}" -f $t.Name, $t.Mode, $t.N, ($out | Select-Object -First 1))
}
Write-Host '--- cadence after acceleration ---'
schtasks /Query /FO CSV 2>$null | ConvertFrom-Csv | Where-Object { $_.TaskName -match 'DSH_MM_' } |
    Select-Object TaskName, @{n = 'Next'; e = { $_.'Next Run Time' } } | Sort-Object TaskName |
    Format-Table -AutoSize | Out-String -Width 80
