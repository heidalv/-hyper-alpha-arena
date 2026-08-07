$ErrorActionPreference = "SilentlyContinue"
$base = "http://127.0.0.1:8000"

function Test-Endpoint {
    param($name, $url)
    Write-Host ""
    Write-Host "=== $name ==="
    try {
        $result = Invoke-RestMethod "$base$url" -TimeoutSec 10
        $json = $result | ConvertTo-Json -Depth 5
        if ($json.Length -gt 1000) {
            Write-Host $json.Substring(0, 1000)
        } else {
            Write-Host $json
        }
        return $true
    } catch {
        $status = [int]$_.Exception.Response.StatusCode
        Write-Host "ERROR $status"
        return $false
    }
}

Test-Endpoint "Health" "/api/health"
Test-Endpoint "FullAuto-Status" "/api/full-auto/status"
Test-Endpoint "Paper-Positions" "/api/paper/positions"
Test-Endpoint "Risk-Status" "/api/risk/status"
