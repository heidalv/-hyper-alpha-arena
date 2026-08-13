$ErrorActionPreference = 'Continue'
$repo = Split-Path -Parent $PSScriptRoot
$log = Join-Path $repo 'logs\desktop-publish.log'
$err = Join-Path $repo 'logs\desktop-publish.err.log'
Remove-Item $log, $err -Force -ErrorAction SilentlyContinue
"=== publish start $(Get-Date -Format o) ===" | Out-File $log -Encoding UTF8
try {
    & (Join-Path $PSScriptRoot 'publish-desktop.ps1') @args *>&1 |
        Tee-Object -FilePath $log -Append
    exit $LASTEXITCODE
} catch {
    ($_ | Out-String) | Tee-Object -FilePath $err -Append
    exit 1
}
