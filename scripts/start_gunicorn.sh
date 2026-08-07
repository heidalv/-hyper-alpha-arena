#!/bin/bash
# Gunicorn 启动脚本 - 解决 macOS objc fork() 问题
cd "$(dirname "$0")/.."

# 清理旧进程
pkill -f "gunicorn backend.main:app" 2>/dev/null || true
sleep 2

# 清理锁文件和日志
rm -f data/.scheduler.lock
> logs/backend.log
> logs/backend.error.log

# macOS fork() 安全设置 + 环境变量
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
set -a
source .env
set +a

# 启动 gunicorn (2 workers)
exec backend/.venv/bin/gunicorn backend.main:app \
  --bind 127.0.0.1:8000 \
  --workers 2 \
  --worker-class uvicorn.workers.UvicornWorker \
  --timeout 120 \
  --daemon \
  --access-logfile logs/backend.log \
  --error-logfile logs/backend.error.log
