# Hyper-Alpha-Arena Windows 一键环境安装脚本
# 用法（PowerShell 管理员或普通用户均可）:
#   cd D:\001Alpha\Hyper-Alpha-Arena
#   Set-ExecutionPolicy -Scope Process Bypass
#   .\scripts\setup-windows.ps1

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Hyper-Alpha-Arena Windows 环境安装" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "项目目录: $ProjectRoot"
Write-Host ""

function Test-Command($name) {
    return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

# --- 1. 检查必要工具 ---
$missing = @()
if (-not (Test-Command python)) { $missing += "Python 3.11+ (https://www.python.org/downloads/)" }
if (-not (Test-Command node))   { $missing += "Node.js 20+ LTS (https://nodejs.org/)" }
if (-not (Test-Command pnpm))   { $missing += "pnpm (npm install -g pnpm)" }

if ($missing.Count -gt 0) {
    Write-Host "缺少以下工具，请先安装:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host "  - $_" }
    exit 1
}

Write-Host "[1/4] 工具检查通过 (Python / Node / pnpm)" -ForegroundColor Green

# --- 2. Python 虚拟环境 ---
$venvPython = Join-Path $ProjectRoot "backend\venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "[2/4] 创建 Python 虚拟环境..." -ForegroundColor Yellow
    Set-Location (Join-Path $ProjectRoot "backend")
    python -m venv venv
    & ".\venv\Scripts\Activate.ps1"
    python -m pip install --upgrade pip
    if (Test-Path "..\requirements.txt") {
        pip install -r ..\requirements.txt
    } else {
        pip install fastapi "uvicorn[standard]" sqlalchemy pydantic requests websockets apscheduler pandas ccxt cryptography psutil pyyaml
    }
    Set-Location $ProjectRoot
} else {
    Write-Host "[2/4] Python 虚拟环境已存在，跳过" -ForegroundColor Green
}

# --- 3. Node 依赖 ---
Write-Host "[3/4] 安装 Node 依赖 (pnpm)..." -ForegroundColor Yellow
pnpm install
Set-Location (Join-Path $ProjectRoot "frontend")
pnpm install
Set-Location $ProjectRoot

# --- 4. 数据目录检查 ---
Write-Host "[4/4] 检查数据文件..." -ForegroundColor Yellow
$dataDir = Join-Path $ProjectRoot "data"
$dbs = @("alpha_arena.db", "alpha_analytics.db", "alpha_market.db")
foreach ($db in $dbs) {
    $path = Join-Path $dataDir $db
    if (Test-Path $path) {
        $size = [math]::Round((Get-Item $path).Length / 1MB, 1)
        Write-Host "  OK  $db ($size MB)" -ForegroundColor Green
    } else {
        Write-Host "  --  $db 不存在（首次运行会自动创建）" -ForegroundColor DarkYellow
    }
}

Write-Host ""
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " 安装完成！" -ForegroundColor Green
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "启动方式（任选其一）:" -ForegroundColor White
Write-Host "  A. 图形界面: 双击 launcher.py（需已安装 Python + tkinter）"
Write-Host "  B. 命令行:   pnpm run dev"
Write-Host "  C. 仅后端:   backend\venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000"
Write-Host ""
Write-Host "浏览器访问: http://localhost:5173 （前端） / http://localhost:8000/docs （API）"
Write-Host ""
Write-Host "若使用币安/Hyperliquid API，请确认 .env 和 frontend\.env 中的密钥已正确配置。"
