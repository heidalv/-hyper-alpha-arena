# [h772] Validate the patched aster-depth-watchdog.ps1 (ASCII only).
$f = 'D:\001Alpha\Hyper-Alpha-Arena\scripts\aster-depth-watchdog.ps1'
$err = $null
$null = [System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$err)
if ($err -and $err.Count -gt 0) {
    Write-Host ('FAIL: ' + $err[0].Message)
} else {
    Write-Host 'OK aster-depth-watchdog.ps1 syntax'
}
