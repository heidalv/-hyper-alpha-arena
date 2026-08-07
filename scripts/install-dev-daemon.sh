#!/usr/bin/env bash
# 安装 / 卸载 macOS launchd 开发守护（登录后自动跑看门狗）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LABEL="com.hyper-alpha-arena.dev"
PLIST_DEST="$HOME/Library/LaunchAgents/${LABEL}.plist"

generate_plist() {
  cat > "$PLIST_DEST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${SCRIPT_DIR}/dev-watchdog.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>${REPO_ROOT}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>${REPO_ROOT}/logs/launchd_dev.log</string>
  <key>StandardErrorPath</key>
  <string>${REPO_ROOT}/logs/launchd_dev.error.log</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>OBJC_DISABLE_INITIALIZE_FORK_SAFETY</key>
    <string>YES</string>
  </dict>
</dict>
</plist>
EOF
}

cmd_install() {
  mkdir -p "$REPO_ROOT/logs" "$REPO_ROOT/.run" "$HOME/Library/LaunchAgents"
  chmod +x "$SCRIPT_DIR/dev-stack.sh" "$SCRIPT_DIR/dev-watchdog.sh"
  generate_plist
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  "$SCRIPT_DIR/dev-stack.sh" stop || true
  generate_plist
  launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
  launchctl enable "gui/$(id -u)/${LABEL}"
  launchctl kickstart -k "gui/$(id -u)/${LABEL}" || true
  echo "[OK] 已安装 launchd 守护: $PLIST_DEST"
  echo "     登录后会自动运行看门狗并拉起前后端"
  echo "     查看: ./scripts/dev-stack.sh status"
}

cmd_uninstall() {
  launchctl bootout "gui/$(id -u)/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST_DEST"
  "$SCRIPT_DIR/dev-stack.sh" stop || true
  echo "[OK] 已卸载 launchd 守护"
}

usage() {
  cat <<EOF
用法: $(basename "$0") install|uninstall|status

  install    写入 ~/Library/LaunchAgents 并立即启动看门狗
  uninstall  移除 launchd 条目并停止开发栈
  status     查看 launchd 与 dev-stack 状态
EOF
}

cmd_status() {
  if launchctl print "gui/$(id -u)/${LABEL}" >/dev/null 2>&1; then
    echo "[OK] launchd 服务已加载: $LABEL"
  else
    echo "[--] launchd 服务未安装"
  fi
  "$SCRIPT_DIR/dev-stack.sh" status
}

main() {
  case "${1:-}" in
    install)   cmd_install ;;
    uninstall) cmd_uninstall ;;
    status)    cmd_status ;;
    *)
      usage
      exit 1
      ;;
  esac
}

main "$@"
