#!/usr/bin/env bash
# 将 Hyper-Alpha-Arena 完整打包，用于迁移到 Windows（含数据库、日志、配置、工作流数据）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE="$(dirname "$ROOT")"
STAMP="$(date +%Y%m%d-%H%M)"
OUT_NAME="001Alpha-Windows迁移包-${STAMP}.tar.gz"
OUT_PATH="${WORKSPACE}/${OUT_NAME}"
PACK_DIR="$(basename "$WORKSPACE")"

echo "=========================================="
echo " Hyper-Alpha-Arena → Windows 迁移打包"
echo "=========================================="
echo "工作区: $WORKSPACE"
echo "输出包: $OUT_PATH"
echo ""

# 打包前提示：若后端正在跑，SQLite 可能不一致
if pgrep -f "uvicorn backend.main:app" >/dev/null 2>&1; then
  echo "⚠️  检测到后端 uvicorn 正在运行，建议先停止再打包，避免数据库损坏。"
  echo "   可执行: cd \"$ROOT\" && pnpm run dev:stop"
  echo ""
fi

echo "正在打包（含 data/ logs/ qaa_workflow/ .env 等，排除 Mac 专用依赖）..."
echo ""

cd "$(dirname "$WORKSPACE")"

tar -czf "$OUT_PATH" \
  --exclude="${PACK_DIR}/Hyper-Alpha-Arena/node_modules" \
  --exclude="${PACK_DIR}/Hyper-Alpha-Arena/frontend/node_modules" \
  --exclude="${PACK_DIR}/Hyper-Alpha-Arena/mobile/node_modules" \
  --exclude="${PACK_DIR}/Hyper-Alpha-Arena/backend/.venv" \
  --exclude="${PACK_DIR}/Hyper-Alpha-Arena/backend/venv" \
  --exclude="${PACK_DIR}/Hyper-Alpha-Arena;C" \
  --exclude="${PACK_DIR}/001Alpha-Windows迁移包-*.tar.gz" \
  --exclude='**/__pycache__' \
  --exclude='**/.pytest_cache' \
  --exclude='**/*.pyc' \
  --exclude='**/.DS_Store' \
  "$PACK_DIR"

SIZE="$(du -h "$OUT_PATH" | cut -f1)"
echo ""
echo "✅ 打包完成"
echo "   文件: $OUT_PATH"
echo "   大小: $SIZE"
echo ""
echo "下一步:"
echo "  1. 把 $OUT_NAME 复制到 Windows（U盘/网盘/局域网）"
echo "  2. 在 Windows 解压后，阅读 迁移指令.txt 或运行 scripts/setup-windows.ps1"
