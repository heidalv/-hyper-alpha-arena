#!/usr/bin/env bash
# 将 frontend/dist 同步到 backend/static，使 http://127.0.0.1:8000 与最新前端一致
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST="$ROOT/frontend/dist"
STATIC="$ROOT/backend/static"

if [[ ! -d "$DIST/index.html" ]] && [[ ! -f "$DIST/index.html" ]]; then
  echo "==> 未找到 frontend/dist，先执行 build ..."
  (cd "$ROOT/frontend" && npm run build)
fi

echo "==> 同步 $DIST -> $STATIC"
mkdir -p "$STATIC"
rm -rf "${STATIC:?}/"*
cp -R "$DIST/"* "$STATIC/"
echo "[OK] backend/static 已更新 — 请刷新 http://127.0.0.1:8000"
