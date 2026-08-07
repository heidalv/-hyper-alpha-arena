"""
Async/Sync 桥接工具

在同步的主循环线程中安全调用异步交易所 API。
提供 run_async(coro) 函数，自动处理事件循环检测和创建。
"""

import asyncio
import logging
import time
from typing import Any, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def run_async(coro: Coroutine[Any, Any, T]) -> T:
    """
    在同步上下文中运行异步协程。

    策略：
    1. 检测是否有运行中的事件循环 -> 使用 run_coroutine_threadsafe
    2. 否则 -> 使用 asyncio.run() 创建新事件循环
    3. 内置重试（3次，100ms 退避）处理 SQLite 写锁冲突

    Args:
        coro: 异步协程对象

    Returns:
        协程的返回值
    """
    max_retries = 3
    backoff = 0.1

    for attempt in range(max_retries):
        try:
            return _run_once(coro)
        except Exception as e:
            err_str = str(e).lower()
            is_locked = "locked" in err_str or "database is locked" in err_str
            if is_locked and attempt < max_retries - 1:
                logger.warning(
                    f"[AsyncBridge] DB 锁冲突，重试 {attempt + 1}/{max_retries}..."
                )
                time.sleep(backoff * (attempt + 1))
                continue
            raise

    # 不应该到这里，但保险起见
    return _run_once(coro)


def _run_once(coro: Coroutine[Any, Any, T]) -> T:
    """单次执行协程"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # 在已有事件循环中（例如 FastAPI 的 async 端点）
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=30)
    else:
        # 没有运行中的事件循环，直接创建新的
        return asyncio.run(coro)


def run_async_safe(coro: Coroutine[Any, Any, T], default: T = None) -> T:
    """
    安全版本的 run_async，异常时返回默认值而不是抛出。

    用于非关键路径的异步调用（如数据采集），避免因单个调用失败导致整个 tick 中断。
    """
    try:
        return run_async(coro)
    except Exception as e:
        logger.debug(f"[AsyncBridge] 异步调用失败（使用默认值）: {e}")
        return default
