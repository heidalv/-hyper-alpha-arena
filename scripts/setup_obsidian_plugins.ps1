<#
.SYNOPSIS
    Download and install Obsidian community plugins (Dataview + Local REST API)
    into the obsidian_vault.
.DESCRIPTION
    Downloads the latest main.js + styles.css from each plugin's GitHub releases
    and places them in the correct .obsidian/plugins/<id>/ directory.
#>

$ErrorActionPreference = "Stop"
$VaultDir = Join-Path $PSScriptRoot "..\obsidian_vault" -Resolve
$Plugins = @(
    @{
        Id = "dataview"
        Name = "Dataview"
        Repo = "blacksmithgu/obsidian-dataview"
    },
    @{
        Id = "obsidian-local-rest-api"
        Name = "Local REST API"
        Repo = "CoddingtonMill/obsidian-local-rest-api"
    }
)

Write-Host "=== Installing Obsidian Community Plugins ===" -ForegroundColor Cyan
Write-Host "Vault: $VaultDir`n"

foreach ($plugin in $Plugins) {
    $pluginDir = Join-Path $VaultDir ".obsidian\plugins\$($plugin.Id)"
    if (-not (Test-Path $pluginDir)) {
        New-Item -ItemType Directory -Path $pluginDir -Force | Out-Null
    }

    Write-Host "[$($plugin.Name)] Downloading..." -ForegroundColor Yellow

    $baseUrl = "https://github.com/$($plugin.Repo)/releases/latest/download"
    $files = @("main.js", "manifest.json", "styles.css")

    foreach ($file in $files) {
        $url = "$baseUrl/$file"
        $outPath = Join-Path $pluginDir $file
        try {
            Invoke-WebRequest -Uri $url -OutFile $outPath -UseBasicParsing -ErrorAction Stop
            $size = (Get-Item $outPath).Length
            Write-Host "  ✓ $file ($($size / 1KB -as [int]) KB)" -ForegroundColor Green
        } catch {
            if ($file -eq "styles.css") {
                Write-Host "  - styles.css skipped (optional)" -ForegroundColor Gray
            } else {
                Write-Warning "  ✗ $file download failed: $_"
            }
        }
    }
}

Write-Host "`n=== Done ===" -ForegroundColor Cyan
Write-Host "Now open Obsidian, go to Settings → Community plugins, and enable:"
Write-Host "  - Dataview" -ForegroundColor Green
Write-Host "  - Local REST API" -ForegroundColor Green
Write-Host "`nThe community-plugins.json already lists them as enabled." -ForegroundColor Cyan
