# [h768] Validate the two patched watchdog scripts (syntax only).
foreach ($f in @(
        'D:\001Alpha\Hyper-Alpha-Arena\scripts\backend-watchdog.ps1',
        'D:\001Alpha\Hyper-Alpha-Arena\scripts\data-center-watchdog.ps1')) {
    $err = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$err)
    if ($err -and $err.Count -gt 0) {
        Write-Host ("FAIL {0}: {1}" -f (Split-Path $f -Leaf), $err[0].Message)
    } else {
        Write-Host ("OK   {0}" -f (Split-Path $f -Leaf))
    }
}
