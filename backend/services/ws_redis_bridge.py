# backend/services/ws_redis_bridge.py
"""Redis pub/sub 桥:跨 worker 广播 WS 消息。

多 worker 下每个 worker 有自己的 ConnectionManager(进程内),
socket 只在持连接的 worker 上。通过 Redis pub/sub:
- 发送方 publish 到 ws:account:{id} / ws:broadcast
- 每个 worker 订阅,收到后投递给本地 socket

REDIS_URL 未设时退化为本地直发(单 worker dev),不强制依赖 Redis。
设置 REDIS_URL(多 worker prod)后自动启用 pub/sub 跨 worker fanout。

设计要点:
- 同步 redis 客户端做 publish(调用方多在非 async 上下文/调度线程)。
- 后台 daemon 线程订阅,收到消息调本地投递回调(由 ConnectionManager 注册)。
- 幂等:start_subscriber 多次调用只起一个线程;无 Redis 时 no-op。
- 失败兜底:Redis 连不上 / publish 失败 → 回退本地直发,不丢消息。
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable, Optional, Union

_log = logging.getLogger(__name__)

# 从 settings 读 REDIS_URL(空字符串=未设=单 worker 本地模式)。
# 延迟导入 settings,避免在 settings 自身初始化期产生循环导入。
def _resolve_redis_url() -> str:
    try:
        from backend.config import settings  # noqa: WPS433 (局部导入避免循环)
        return getattr(settings, "REDIS_URL", "") or ""
    except Exception:
        # settings 不可用时退回 os.getenv,保持独立可用(测试场景)。
        import os
        return os.getenv("REDIS_URL", "") or ""


_redis_client = None  # 同步 publish 客户端(lazy)
_pubsub_thread: Optional[threading.Thread] = None
# 本地投递回调:dispatch(kind, account_id, message)
#   kind="account" → account_id 为目标账号 int
#   kind="broadcast" → account_id 为 None
_local_dispatch: Optional[Callable[[str, Optional[int], dict], None]] = None


def _get_redis():
    """Lazy 建立同步 Redis 客户端。连接失败返回 None(调用方走本地兜底)。"""
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    url = _resolve_redis_url()
    if not url:
        return None
    try:
        import redis  # 延迟导入:无 Redis 环境不强依赖
        _redis_client = redis.Redis.from_url(url, decode_responses=True)
        _redis_client.ping()
    except Exception as e:  # 连接失败、库缺失等
        _log.warning("Redis connect failed, WS broadcast 退化为本地直发: %s", e)
        _redis_client = None
    return _redis_client


def configure_local_dispatch(
    dispatch: Callable[[str, Optional[int], dict], None],
) -> None:
    """注册本地投递回调(由 ConnectionManager 调用)。

    dispatch(kind, account_id, message):
        kind="account"   → 对 account_id 的本地 socket 投递 message
        kind="broadcast" → 对全部本地 socket 投递 message(account_id=None)
    """
    global _local_dispatch
    _local_dispatch = dispatch


def publish_to_account(account_id: int, message: dict) -> None:
    """向指定账号广播(跨 worker)。无 Redis 时直接本地投递。"""
    r = _get_redis()
    if r is None:
        # 无 Redis(单 worker):直接本地投递
        if _local_dispatch is not None:
            _local_dispatch("account", account_id, message)
        return
    try:
        r.publish("ws:account:" + str(account_id), json.dumps(message, ensure_ascii=False))
    except Exception as e:
        _log.warning("Redis publish(account) failed, 本地兜底: %s", e)
        if _local_dispatch is not None:
            _local_dispatch("account", account_id, message)


def publish_broadcast(message: dict) -> None:
    """向全部账号广播(跨 worker)。无 Redis 时直接本地投递。"""
    r = _get_redis()
    if r is None:
        if _local_dispatch is not None:
            _local_dispatch("broadcast", None, message)
        return
    try:
        r.publish("ws:broadcast", json.dumps(message, ensure_ascii=False))
    except Exception as e:
        _log.warning("Redis publish(broadcast) failed, 本地兜底: %s", e)
        if _local_dispatch is not None:
            _local_dispatch("broadcast", None, message)


def start_subscriber() -> None:
    """启动后台线程订阅 Redis 频道,收到消息调 _local_dispatch 投递本地 socket。

    幂等:多次调用只起一个线程。无 Redis / 未配 dispatch 时 no-op。
    """
    global _pubsub_thread
    if _pubsub_thread is not None:
        return
    if not _resolve_redis_url():
        return
    r = _get_redis()
    if r is None:
        return

    def _loop():
        try:
            pubsub = r.pubsub()
            # psubscribe 订阅 ws:account:* 和 ws:broadcast
            pubsub.psubscribe("ws:account:*", "ws:broadcast")
            for msg in pubsub.listen():
                if msg.get("type") not in ("pmessage", "message"):
                    continue
                try:
                    channel = msg.get("channel", "") or ""
                    data = msg.get("data", "")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")
                    payload = json.loads(data)
                    if channel == "ws:broadcast":
                        if _local_dispatch is not None:
                            _local_dispatch("broadcast", None, payload)
                    elif channel.startswith("ws:account:"):
                        acct = int(channel.split(":")[-1])
                        if _local_dispatch is not None:
                            _local_dispatch("account", acct, payload)
                except Exception as e:
                    _log.warning("WS redis sub parse failed: %s", e)
        except Exception as e:
            _log.error("WS redis subscriber thread crashed: %s", e)

    _pubsub_thread = threading.Thread(target=_loop, name="ws-redis-sub", daemon=True)
    _pubsub_thread.start()
    _log.info("WS Redis subscriber started")


def _reset_for_tests() -> None:
    """测试辅助:重置模块级状态(不对外部生产代码承诺)。"""
    global _redis_client, _pubsub_thread, _local_dispatch
    _redis_client = None
    _pubsub_thread = None
    _local_dispatch = None
