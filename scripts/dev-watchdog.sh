#!/usr/bin/env bash
# 看门狗：定期检查 backend/frontend，挂了自动拉起
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
WATCHDOG_PID_FILE="$REPO_ROOT/.run/watchdog.pid"
mkdir -p "$REPO_ROOT/.run"
echo $$ > "$WATCHDOG_PID_FILE"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
WATCH_INTERVAL="${WATCH_INTERVAL:-15}"

export BACKEND_PORT FRONTEND_PORT WATCH_INTERVAL

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] [watchdog] $*"
}

http_ok() {
  local url="$1"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$url" 2>/dev/null || echo "000")"
  [[ "$code" == "200" ]]
}

log "started (interval=${WATCH_INTERVAL}s, backend=:${BACKEND_PORT}, frontend=:${FRONTEND_PORT})"
"$SCRIPT_DIR/dev-stack.sh" start >/dev/null 2>&1 || log "initial start failed"

while true; do
  backend_ok=false
  frontend_ok=false
  http_ok "http://127.0.0.1:${BACKEND_PORT}/api/account/list" && backend_ok=true
  http_ok "http://127.0.0.1:${FRONTEND_PORT}/" && frontend_ok=true

  if ! $backend_ok || ! $frontend_ok; then
    log "unhealthy backend=$backend_ok frontend=$frontend_ok -> restarting stack"
    "$SCRIPT_DIR/dev-stack.sh" start >/dev/null 2>&1 || log "stack restart failed"
  fi

  sleep "$WATCH_INTERVAL"
done
