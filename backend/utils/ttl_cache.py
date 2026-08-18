"""进程内 TTL 缓存工具（GUI 高频轮询端点加速）。

背景（2026-08-18 性能治理）：主 API 进程内交易循环（scalp/unified/midlong、
进化、快照采集）长期占用 GIL，HTTP 请求线程在 GIL 队列里排队，即使「纯查询」
端点也会被拖到 3~13s。对轮询型只读端点做秒级进程内缓存，命中路径几乎不抢
GIL，页面即可秒开；缓存 TTL 均为秒级，业务口径不受影响（轮询间隔本身 ≥3s）。

线程安全：写操作持锁；读操作读整表指针（dict 引用替换），GIL 保证原子性。
容量上限防内存膨胀（LRU 近似：超限时按时间戳剔除最旧 20%）。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, Tuple

_store: Dict[str, Tuple[float, Any]] = {}
_lock = threading.Lock()
_MAX_ENTRIES = 512


def _evict_locked() -> None:
    if len(_store) <= _MAX_ENTRIES:
        return
    items = sorted(_store.items(), key=lambda kv: kv[1][0])
    for k, _ in items[: max(1, len(items) // 5)]:
        _store.pop(k, None)


def ttl_get(key: str, max_age_sec: float) -> Any:
    """命中返回缓存值，未命中/过期返回 None。"""
    entry = _store.get(key)
    if entry is None:
        return None
    ts, val = entry
    if time.time() - ts > max_age_sec:
        return None
    return val


def ttl_set(key: str, value: Any) -> None:
    with _lock:
        _store[key] = (time.time(), value)
        _evict_locked()


def ttl_cached(key: str, max_age_sec: float, producer: Callable[[], Any]) -> Any:
    """读缓存；miss 时执行 producer 并回填。producer 异常向上抛、不缓存。"""
    entry = _store.get(key)
    if entry is not None:
        ts, val = entry
        if time.time() - ts <= max_age_sec:
            return val
    value = producer()
    ttl_set(key, value)
    return value


def ttl_invalidate(prefix: str = "") -> None:
    """按前缀失效（prefix 为空则全清）。"""
    with _lock:
        if not prefix:
            _store.clear()
            return
        for k in [k for k in _store if k.startswith(prefix)]:
            _store.pop(k, None)


def ttl_stats() -> Dict[str, Any]:
    with _lock:
        return {"entries": len(_store), "keys": list(_store.keys())[:20]}
