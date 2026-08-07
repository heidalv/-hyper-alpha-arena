#!/usr/bin/env bash
# Hyper-Alpha-Arena 本地开发栈：一键启动 / 自检 / 清僵尸 / 看门狗
# 用法:
#   ./scripts/dev-stack.sh up              # 推荐：自检 → 清僵尸 → 启动全部 → 验收
#   ./scripts/start-all.sh                 # 同上（快捷入口）
#   ./scripts/dev-stack.sh start [--watch] # 仅启动
#   ./scripts/dev-stack.sh cleanup         # 仅清理僵尸/陈旧 pid
#   ./scripts/dev-stack.sh check           # 仅自检
#   ./scripts/dev-stack.sh stop | restart | status | logs

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
RUN_DIR="$REPO_ROOT/.run"
LOG_DIR="$REPO_ROOT/logs"
FRONTEND_DIR="$REPO_ROOT/frontend"

BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
WATCH_INTERVAL="${WATCH_INTERVAL:-15}"

BACKEND_PORTS=(8000 8001)
FRONTEND_PORTS=(5173 5174 5175)

BACKEND_PID_FILE="$RUN_DIR/backend.pid"
FRONTEND_PID_FILE="$RUN_DIR/frontend.pid"
WATCHDOG_PID_FILE="$RUN_DIR/watchdog.pid"
BACKEND_LOG="$LOG_DIR/backend_dev.log"
FRONTEND_LOG="$LOG_DIR/frontend_dev.log"
WATCHDOG_LOG="$LOG_DIR/watchdog.log"

mkdir -p "$RUN_DIR" "$LOG_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

log_step() { echo -e "${CYAN}==>${NC} $*"; }
log_ok()   { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_err()  { echo -e "${RED}[ERR]${NC} $*"; }

port_pids() {
  lsof -ti "tcp:$1" -sTCP:LISTEN 2>/dev/null | sort -u || true
}

port_pid() {
  port_pids "$1" | head -1
}

is_uvicorn_worker_cmd() {
  local cmd="$1"
  [[ "$cmd" == *"multiprocessing"* && "$cmd" == *"spawn_main"* ]]
}

is_zombie_pid() {
  local pid="$1"
  [[ -n "$pid" ]] || return 1
  local state
  state="$(ps -p "$pid" -o state= 2>/dev/null | tr -d ' ' || true)"
  [[ "$state" == "Z" ]]
}

cleanup_stale_pid_files() {
  local file label pid
  for pair in \
    "$BACKEND_PID_FILE:backend" \
    "$FRONTEND_PID_FILE:frontend" \
    "$WATCHDOG_PID_FILE:watchdog"; do
    file="${pair%%:*}"
    label="${pair##*:}"
    pid="$(read_pid_file "$file")"
    if [[ -n "$pid" ]] && ! is_alive "$pid"; then
      log_warn "移除陈旧 $label pid 文件 (pid=$pid 已不存在)"
      rm -f "$file"
    fi
  done
}

cleanup_orphan_uvicorn_workers() {
  local pid cmd pp alive
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    cmd="$(cmdline_for_pid "$pid")"
    if ! is_uvicorn_worker_cmd "$cmd"; then
      continue
    fi
    pp="$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ' || true)"
    alive=false
    if [[ -n "$pp" ]] && is_alive "$pp"; then
      alive=true
    fi
    if ! $alive; then
      log_step "清理 orphan uvicorn worker (pid=$pid parent=$pp dead)"
      kill -9 "$pid" 2>/dev/null || true
    fi
  done < <(pgrep -f "spawn_main" 2>/dev/null || true)
}

cleanup_duplicate_port_listeners() {
  local port pid cmd pids
  for port in "${BACKEND_PORTS[@]}" "${FRONTEND_PORTS[@]}"; do
    pids="$(port_pids "$port")"
    [[ -z "$pids" ]] && continue
    local count=0
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      count=$((count + 1))
    done <<< "$pids"
    if [[ "$count" -le 1 ]]; then
      continue
    fi
    if [[ "$port" == "$BACKEND_PORT" ]] && [[ "$(http_code "http://127.0.0.1:${BACKEND_PORT}/api/account/list" 3)" == "200" ]]; then
      log_ok "端口 $port 有 $count 个进程但 API 正常，跳过清理"
      continue
    fi
    if [[ "$port" == "$FRONTEND_PORT" ]] && [[ "$(http_code "http://127.0.0.1:${FRONTEND_PORT}/" 3)" == "200" ]]; then
      log_ok "端口 $port 有 $count 个进程但页面正常，跳过清理"
      continue
    fi
    log_warn "端口 $port 有 $count 个监听进程且服务异常，清理重复项"
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      cmd="$(cmdline_for_pid "$pid")"
      if [[ "$port" == "$BACKEND_PORT" ]] && is_our_backend_cmd "$cmd"; then
        kill_pid_gracefully "$pid" "duplicate backend@:$port"
        pkill -P "$pid" 2>/dev/null || true
      elif [[ "$port" == "$FRONTEND_PORT" ]] && is_our_frontend_cmd "$cmd"; then
        kill_pid_gracefully "$pid" "duplicate frontend@:$port"
        pkill -P "$pid" 2>/dev/null || true
      elif is_uvicorn_worker_cmd "$cmd"; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    done <<< "$pids"
  done
}

cleanup_zombie_processes() {
  local pid state cmd
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    if is_zombie_pid "$pid"; then
      cmd="$(cmdline_for_pid "$pid")"
      log_warn "发现僵尸进程 pid=$pid (${cmd:0:80}...)"
      local pp
      pp="$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ' || true)"
      if [[ -n "$pp" ]] && is_alive "$pp"; then
        log_step "向父进程 $pp 发送 SIGCHLD 回收"
        kill -s SIGCHLD "$pp" 2>/dev/null || true
      fi
    fi
  done < <(pgrep -f "uvicorn backend.main:app|vite|dev-watchdog|dev-stack" 2>/dev/null || true)
}

cleanup_conflicting_ports() {
  local port pid cmd
  for port in "$BACKEND_PORT" "$FRONTEND_PORT"; do
    while IFS= read -r pid; do
      [[ -z "$pid" ]] && continue
      cmd="$(cmdline_for_pid "$pid")"
      if [[ "$port" == "$BACKEND_PORT" ]] && ! is_our_backend_cmd "$cmd" && ! is_uvicorn_worker_cmd "$cmd"; then
        log_warn "端口 $port 被非本项目进程占用 pid=$pid，跳过（需手动处理）"
      elif [[ "$port" == "$FRONTEND_PORT" ]] && ! is_our_frontend_cmd "$cmd"; then
        log_warn "端口 $port 被非本项目进程占用 pid=$pid，跳过（需手动处理）"
      fi
    done <<< "$(port_pids "$port")"
  done
}

cmd_cleanup() {
  log_step "[1/1] 清理僵尸进程、orphan worker、重复监听、陈旧 pid ..."
  cleanup_stale_pid_files
  cleanup_orphan_uvicorn_workers
  cleanup_zombie_processes
  cleanup_duplicate_port_listeners
  cleanup_conflicting_ports
  log_ok "清理完成"
}

check_preflight() {
  local failed=0
  echo "---------- 启动前自检 ----------"
  if [[ -f "$REPO_ROOT/.env" ]]; then
    log_ok ".env 存在"
  else
    log_warn ".env 不存在（部分功能可能不可用）"
  fi
  if [[ -x "$REPO_ROOT/backend/.venv/bin/uvicorn" ]]; then
    log_ok "backend 虚拟环境就绪"
  else
    log_err "backend/.venv 未安装，请运行: cd backend && uv sync"
    failed=1
  fi
  if [[ -d "$FRONTEND_DIR/node_modules" ]]; then
    log_ok "frontend 依赖已安装"
  else
    log_warn "frontend/node_modules 缺失，尝试 npm install ..."
    (cd "$FRONTEND_DIR" && npm install --silent) || { log_err "frontend npm install 失败"; failed=1; }
  fi
  local bp fp
  bp="$(port_pid "$BACKEND_PORT")"
  fp="$(port_pid "$FRONTEND_PORT")"
  if [[ -n "$bp" ]]; then
    if is_our_backend_cmd "$(cmdline_for_pid "$bp")"; then
      log_ok "backend 端口 $BACKEND_PORT 已被本项目占用 pid=$bp"
    else
      log_err "backend 端口 $BACKEND_PORT 被其他程序占用 pid=$bp"
      failed=1
    fi
  else
    log_ok "backend 端口 $BACKEND_PORT 空闲"
  fi
  if [[ -n "$fp" ]]; then
    if is_our_frontend_cmd "$(cmdline_for_pid "$fp")"; then
      log_ok "frontend 端口 $FRONTEND_PORT 已被本项目占用 pid=$fp"
    else
      log_err "frontend 端口 $FRONTEND_PORT 被其他程序占用 pid=$fp"
      failed=1
    fi
  else
    log_ok "frontend 端口 $FRONTEND_PORT 空闲"
  fi
  if command -v pg_isready >/dev/null 2>&1; then
    if pg_isready -q 2>/dev/null; then
      log_ok "PostgreSQL 可连接"
    else
      log_warn "PostgreSQL 未响应（若不用 PG 可忽略）"
    fi
  fi
  echo "--------------------------------"
  return "$failed"
}

check_postflight() {
  local failed=0
  echo ""
  echo "---------- 启动后验收 ----------"
  local bcode fcode i
  bcode="000"
  fcode="000"
  for i in $(seq 1 15); do
    bcode="$(http_code "http://127.0.0.1:${BACKEND_PORT}/api/account/list" 8)"
    fcode="$(http_code "http://127.0.0.1:${FRONTEND_PORT}/" 5)"
    if [[ "$bcode" == "200" && "$fcode" == "200" ]]; then
      break
    fi
    sleep 1
  done
  if [[ "$bcode" == "200" ]]; then
    log_ok "backend API 正常 HTTP $bcode"
  else
    log_err "backend API 异常 HTTP $bcode"
    failed=1
  fi
  if [[ "$fcode" == "200" ]]; then
    log_ok "frontend 页面正常 HTTP $fcode"
  else
    log_err "frontend 页面异常 HTTP $fcode"
    failed=1
  fi
  local wp
  wp="$(read_pid_file "$WATCHDOG_PID_FILE")"
  if is_alive "$wp" || launchctl print "gui/$(id -u)/com.hyper-alpha-arena.dev" >/dev/null 2>&1; then
    log_ok "看门狗运行中"
  else
    log_warn "看门狗未运行"
  fi
  if [[ -x "$REPO_ROOT/backend/.venv/bin/python" ]]; then
    if "$REPO_ROOT/backend/.venv/bin/python" -c "import backend.main" >/dev/null 2>&1; then
      log_ok "backend.main 模块可导入"
    else
      log_warn "backend.main 导入检查失败（服务可能仍在启动中）"
    fi
  fi
  echo "--------------------------------"
  return "$failed"
}

cmd_check() {
  check_preflight
  check_postflight || true
}

cmd_up() {
  local watch=true
  for arg in "$@"; do
    case "$arg" in
      --no-watch) watch=false ;;
    esac
  done

  echo "========================================"
  echo " Hyper-Alpha-Arena 一键启动"
  echo "========================================"

  check_preflight || true
  echo ""
  cmd_cleanup
  echo ""

  # 若端口上有旧实例但 HTTP 不健康，强制清掉再启
  if [[ "$(http_code "http://127.0.0.1:${BACKEND_PORT}/api/account/list" 3)" != "200" ]]; then
    kill_backend_tree
  fi
  if [[ "$(http_code "http://127.0.0.1:${FRONTEND_PORT}/" 3)" != "200" ]]; then
    kill_frontend_tree
  fi

  start_backend || true
  start_frontend || true

  if $watch; then
    if launchctl print "gui/$(id -u)/com.hyper-alpha-arena.dev" >/dev/null 2>&1; then
      launchctl kickstart -k "gui/$(id -u)/com.hyper-alpha-arena.dev" 2>/dev/null || true
      log_ok "launchd 看门狗已激活"
    else
      start_watchdog
    fi
  fi

  echo ""
  if check_postflight; then
    echo ""
    log_ok "全部就绪 — 打开 http://127.0.0.1:${FRONTEND_PORT}"
    cmd_status
    return 0
  fi
  echo ""
  log_err "部分服务未通过验收，请查看日志: ./scripts/dev-stack.sh logs all"
  cmd_status
  return 1
}

http_code() {
  curl -s -o /dev/null -w '%{http_code}' --max-time "${2:-5}" "$1" 2>/dev/null || echo "000"
}

is_alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

read_pid_file() {
  local file="$1"
  if [[ -f "$file" ]]; then
    tr -d '[:space:]' < "$file"
  fi
}

is_our_backend_cmd() {
  local cmd="$1"
  [[ "$cmd" == *"uvicorn"* && "$cmd" == *"backend.main:app"* ]]
}

is_our_frontend_cmd() {
  local cmd="$1"
  [[ "$cmd" == *"vite"* || "$cmd" == *"npm run dev"* || "$cmd" == *"pnpm dev"* ]]
}

kill_all_our_backends() {
  local pid cmd
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    cmd="$(cmdline_for_pid "$pid")"
    if is_our_backend_cmd "$cmd"; then
      kill_pid_gracefully "$pid" "backend(uvicorn)"
      pkill -P "$pid" 2>/dev/null || true
    fi
  done < <(pgrep -f "uvicorn backend.main:app" 2>/dev/null || true)
}

cmdline_for_pid() {
  ps -p "$1" -o command= 2>/dev/null | sed 's/^[[:space:]]*//' || true
}

kill_pid_gracefully() {
  local pid="$1"
  local label="$2"
  if ! is_alive "$pid"; then
    return 0
  fi
  log_step "停止 $label (pid=$pid)"
  kill "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    is_alive "$pid" || return 0
    sleep 1
  done
  kill -9 "$pid" 2>/dev/null || true
}

kill_by_port_if_ours() {
  local port="$1"
  local kind="$2"
  local pid
  pid="$(port_pid "$port")"
  [[ -z "$pid" ]] && return 0
  local cmd
  cmd="$(cmdline_for_pid "$pid")"
  if [[ "$kind" == "backend" ]] && is_our_backend_cmd "$cmd"; then
    kill_pid_gracefully "$pid" "backend@:$port"
  elif [[ "$kind" == "frontend" ]] && is_our_frontend_cmd "$cmd"; then
    kill_pid_gracefully "$pid" "frontend@:$port"
  fi
}

kill_backend_tree() {
  local pid="${1:-}"
  if [[ -z "$pid" ]]; then
    pid="$(read_pid_file "$BACKEND_PID_FILE")"
  fi
  if is_alive "$pid"; then
    kill_pid_gracefully "$pid" "backend"
    pkill -P "$pid" 2>/dev/null || true
  fi
  rm -f "$BACKEND_PID_FILE"
  kill_all_our_backends
  kill_by_port_if_ours "$BACKEND_PORT" backend
}

kill_frontend_tree() {
  local pid="${1:-}"
  if [[ -z "$pid" ]]; then
    pid="$(read_pid_file "$FRONTEND_PID_FILE")"
  fi
  if is_alive "$pid"; then
    kill_pid_gracefully "$pid" "frontend"
    pkill -P "$pid" 2>/dev/null || true
  fi
  rm -f "$FRONTEND_PID_FILE"
  kill_by_port_if_ours "$FRONTEND_PORT" frontend
}

kill_watchdog() {
  local pid
  pid="$(read_pid_file "$WATCHDOG_PID_FILE")"
  if is_alive "$pid"; then
    kill_pid_gracefully "$pid" "watchdog"
  fi
  rm -f "$WATCHDOG_PID_FILE"
}

load_env() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
  fi
}

start_backend() {
  if [[ "$(http_code "http://127.0.0.1:${BACKEND_PORT}/api/account/list")" == "200" ]]; then
    log_ok "backend 已在运行 (port $BACKEND_PORT)"
    return 0
  fi

  kill_backend_tree

  log_step "启动 backend (port $BACKEND_PORT) ..."
  load_env
  export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

  (
    cd "$REPO_ROOT"
    uv sync --directory backend --quiet >/dev/null 2>&1 || true
    nohup backend/.venv/bin/uvicorn backend.main:app \
      --reload \
      --reload-dir backend \
      --reload-include '*.py' \
      --reload-exclude 'backend/static/*' \
      --reload-exclude 'backend/data/*' \
      --reload-exclude 'logs/*' \
      --reload-exclude '*.log' \
      --reload-exclude '*.lock' \
      --port "$BACKEND_PORT" \
      --host 0.0.0.0 \
      >> "$BACKEND_LOG" 2>&1 &
    echo $! > "$BACKEND_PID_FILE"
  )

  local pid
  pid="$(read_pid_file "$BACKEND_PID_FILE")"
  for i in $(seq 1 40); do
    if [[ "$(http_code "http://127.0.0.1:${BACKEND_PORT}/api/account/list" 10)" == "200" ]]; then
      log_ok "backend 就绪 pid=$pid url=http://127.0.0.1:${BACKEND_PORT} log=$BACKEND_LOG"
      return 0
    fi
    sleep 1
  done
  log_warn "backend 启动超时，请查看 $BACKEND_LOG"
  return 1
}

start_frontend() {
  if [[ "$(http_code "http://127.0.0.1:${FRONTEND_PORT}/")" == "200" ]]; then
    log_ok "frontend 已在运行 (port $FRONTEND_PORT)"
    return 0
  fi

  kill_frontend_tree

  log_step "启动 frontend (port $FRONTEND_PORT) ..."
  (
    cd "$FRONTEND_DIR"
    nohup npm run dev >> "$FRONTEND_LOG" 2>&1 &
    echo $! > "$FRONTEND_PID_FILE"
  )

  local pid
  pid="$(read_pid_file "$FRONTEND_PID_FILE")"
  for i in $(seq 1 20); do
    if [[ "$(http_code "http://127.0.0.1:${FRONTEND_PORT}/")" == "200" ]]; then
      log_ok "frontend 就绪 pid=$pid url=http://127.0.0.1:${FRONTEND_PORT} log=$FRONTEND_LOG"
      return 0
    fi
    sleep 1
  done
  log_warn "frontend 启动超时，请查看 $FRONTEND_LOG"
  return 1
}

cmd_start() {
  local watch=false
  for arg in "$@"; do
    if [[ "$arg" == "--watch" || "$arg" == "-w" ]]; then
      watch=true
    fi
  done

  echo "========================================"
  echo " Hyper-Alpha-Arena dev stack (macOS)"
  echo "========================================"

  start_backend || true
  start_frontend || true

  if $watch; then
    start_watchdog
  fi

  echo ""
  cmd_status
}

start_watchdog() {
  local existing
  existing="$(read_pid_file "$WATCHDOG_PID_FILE")"
  if is_alive "$existing"; then
    log_ok "watchdog 已在运行 pid=$existing"
    return 0
  fi

  log_step "启动 watchdog (每 ${WATCH_INTERVAL}s 检查) ..."
  nohup "$SCRIPT_DIR/dev-watchdog.sh" >> "$WATCHDOG_LOG" 2>&1 &
  echo $! > "$WATCHDOG_PID_FILE"
  sleep 1
  log_ok "watchdog 已启动 pid=$(read_pid_file "$WATCHDOG_PID_FILE") log=$WATCHDOG_LOG"
}

cmd_stop() {
  log_step "停止开发栈 ..."
  kill_watchdog
  kill_frontend_tree
  kill_backend_tree
  log_ok "已停止 backend / frontend / watchdog"
}

cmd_restart() {
  cmd_stop
  sleep 1
  cmd_start "$@"
  sync_frontend_static || true
}

sync_frontend_static() {
  if [[ -x "$SCRIPT_DIR/sync-frontend-static.sh" ]]; then
    log_step "同步 frontend → backend/static (端口 8000) ..."
    "$SCRIPT_DIR/sync-frontend-static.sh"
  fi
}

cmd_status() {
  local bp fp wp bcode fcode
  bp="$(read_pid_file "$BACKEND_PID_FILE")"
  fp="$(read_pid_file "$FRONTEND_PID_FILE")"
  wp="$(read_pid_file "$WATCHDOG_PID_FILE")"
  bcode="$(http_code "http://127.0.0.1:${BACKEND_PORT}/api/account/list")"
  fcode="$(http_code "http://127.0.0.1:${FRONTEND_PORT}/")"

  echo "========== dev stack status =========="
  if [[ "$bcode" == "200" ]]; then
    log_ok "backend  http://127.0.0.1:${BACKEND_PORT}  pid=${bp:-$(port_pid "$BACKEND_PORT")}  HTTP $bcode"
  else
    log_warn "backend  port $BACKEND_PORT  HTTP $bcode  pid=${bp:-none}"
  fi
  if [[ "$fcode" == "200" ]]; then
    log_ok "frontend http://127.0.0.1:${FRONTEND_PORT}  pid=${fp:-$(port_pid "$FRONTEND_PORT")}  HTTP $fcode"
  else
    log_warn "frontend port $FRONTEND_PORT  HTTP $fcode  pid=${fp:-none}"
  fi
  if is_alive "$wp"; then
    log_ok "watchdog pid=$wp interval=${WATCH_INTERVAL}s log=$WATCHDOG_LOG"
  elif launchctl print "gui/$(id -u)/com.hyper-alpha-arena.dev" >/dev/null 2>&1; then
    log_ok "watchdog launchd 守护中 log=$WATCHDOG_LOG"
  else
    echo -e "${YELLOW}[--]${NC} watchdog 未运行（可用 start --watch 或 install-dev-daemon.sh install）"
  fi
  echo "logs: backend=$BACKEND_LOG frontend=$FRONTEND_LOG watchdog=$WATCHDOG_LOG"
}

cmd_logs() {
  local target="${1:-all}"
  case "$target" in
    backend)  tail -n 80 -f "$BACKEND_LOG" ;;
    frontend) tail -n 80 -f "$FRONTEND_LOG" ;;
    watchdog) tail -n 80 -f "$WATCHDOG_LOG" ;;
    all)
      tail -n 40 "$BACKEND_LOG" "$FRONTEND_LOG" "$WATCHDOG_LOG" 2>/dev/null || true
      ;;
    *)
      log_err "未知日志目标: $target (backend|frontend|watchdog|all)"
      exit 1
      ;;
  esac
}

usage() {
  cat <<EOF
用法: $(basename "$0") <command> [options]
      ./scripts/start-all.sh          # 等同 dev-stack.sh up

命令:
  up [--no-watch]      一键：自检 → 清僵尸 → 启动全部 → 验收（推荐）
  cleanup              仅清理僵尸/orphan/重复监听/陈旧 pid
  check                启动前 + 启动后自检
  start [--watch|-w]   后台启动前后端
  stop                 停止全部
  restart [--watch]    重启
  status               查看状态
  logs [target]        backend|frontend|watchdog|all

环境变量:
  BACKEND_PORT   默认 8000
  FRONTEND_PORT  默认 5173
  WATCH_INTERVAL 看门狗间隔秒数，默认 15

登录自启:
  ./scripts/install-dev-daemon.sh install
  ./scripts/install-dev-daemon.sh uninstall
EOF
}

main() {
  local cmd="${1:-status}"
  shift || true
  case "$cmd" in
    up)      cmd_up "$@" ;;
    cleanup) cmd_cleanup ;;
    check)   cmd_check ;;
    start)   cmd_start "$@" ;;
    stop)    cmd_stop ;;
    restart) cmd_restart "$@" ;;
    status)  cmd_status ;;
    logs)    cmd_logs "${1:-all}" ;;
    -h|--help|help) usage ;;
    *)
      log_err "未知命令: $cmd"
      usage
      exit 1
      ;;
  esac
}

main "$@"
