# deploy/gunicorn.conf.py
"""Gunicorn 配置:多 worker 部署 UvicornWorker。

用法(在仓库根目录):
    gunicorn -c deploy/gunicorn.conf.py main:app

跨 worker WebSocket 广播:
    每个 worker 是独立进程,各自持有自己的 ConnectionManager(进程内 dict)。
    socket 只存在于"持有连接的那个 worker"。要让任意 worker 发起的推送到达
    目标 socket,必须设置 REDIS_URL(见 backend/services/ws_redis_bridge.py),
    通过 Redis pub/sub 在所有 worker 间 fanout。REDIS_URL 未设时退化为本 worker
    本地直发(仅适用于单 worker / 本地开发)。
"""
import os

bind = "127.0.0.1:8000"  # 只监听本地,Nginx 反代到公网
workers = int(os.getenv("WEB_CONCURRENCY", "8"))  # 默认 8 worker(8 核)
worker_class = "uvicorn.workers.UvicornWorker"
timeout = 120
keepalive = 5
preload_app = True  # 共享 import(省内存);注意:WS ConnectionManager 仍每 worker 独立(进程)
# 注:preload_app=True 时模块级代码只跑一次 import,但每个 worker 仍是独立进程,
# ConnectionManager 实例每 worker 一个 → 需要 Redis pub/sub 跨 worker(见 ws_redis_bridge)。
