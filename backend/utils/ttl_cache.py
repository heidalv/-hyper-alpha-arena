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


# ── [2026-09-01 F32] stale-while-revalidate ─────────────────────────
# 背景：交易循环长期饱和 GIL，ops/intel 聚合端点的 fresh 重算在活进程里
# 8-10s（独立进程 0.4-3s）。单纯调大 TTL 只降低频率，每次过期后的首个
# 请求仍要等 8-10s——页面切换正好撞上就卡。stale 模式：过期后立即返回
# 旧值，后台线程单飞刷新；页面切换永不等待 fresh 重算。
_refreshing: Dict[str, bool] = {}
_refresh_lock = threading.Lock()


def _refresh_bg(key: str, producer: Callable[[], Any]) -> None:
    try:
        # 后台线程无请求上下文：套 system_identity 保证 RLS 直通
        # （与调度任务同口径；producer 自身也会 set 身份，双保险）
        try:
            from backend.core.tenant import system_identity
            with system_identity():
                value = producer()
        except Exception:
            value = producer()
        ttl_set(key, value)
    except Exception:
        pass  # 刷新失败保留旧值，下个周期再试
    finally:
        with _refresh_lock:
            _refreshing[key] = False


def ttl_cached_stale(key: str, max_age_sec: float, producer: Callable[[], Any]) -> Any:
    """stale-while-revalidate：新鲜命中直返；过期返回旧值并后台单飞刷新；
    无缓存时同步计算（首次请求照常付成本）。"""
    entry = _store.get(key)
    if entry is not None:
        ts, val = entry
        now = time.time()
        if now - ts <= max_age_sec:
            return val
        # 过期：返回旧值 + 后台刷新（单飞防惊群）
        with _refresh_lock:
            if not _refreshing.get(key):
                _refreshing[key] = True
                _t = threading.Thread(
                    target=_refresh_bg, args=(key, producer),
                    daemon=True, name=f"ttl-stale-{key[:24]}",
                )
                _t.start()
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
