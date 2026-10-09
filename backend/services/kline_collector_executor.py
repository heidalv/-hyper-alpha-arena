"""K 线采集专用线程池 — 与 FastAPI/uvicorn 默认 executor 隔离。

采集器的 HTTP 拉取、DB 写入等同步 I/O 全部走此池，避免与 API 请求抢线程。
"""
from __future__ import annotations

import asyncio
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None


def get_kline_collector_executor() -> ThreadPoolExecutor:
    """懒加载单例：K 线采集专用线程池。"""
    global _executor
    if _executor is None:
        with _lock:
            if _executor is None:
                max_workers = int(os.getenv("KLINE_COLLECTOR_MAX_WORKERS", "6"))
                # [续作R19] 上限 16→64：周期对齐（KLINE_P0_PERIOD_ALIGN）后任务量
                # 318→约162/轮，但 12 线程 × ~1s 单任务 ⇒ 队尾等待 ≈13.5s 仍 > 6s 单请求超时
                # （实测 601 轮：成功率中位 94.8%、0ok 2.3%，未达预写死阈值 ≥98%/1%）。
                # 48 线程 ⇒ 队尾 ≈3.4s < 6s。上限放 64 留余量，实际值仍由 env 决定。
                max_workers = max(2, min(max_workers, 64))
                _executor = ThreadPoolExecutor(
                    max_workers=max_workers,
                    thread_name_prefix="kline-collector",
                )
    return _executor


async def run_kline_io(func: Callable[..., T], *args, **kwargs) -> T:
    """在采集专用线程池中执行同步 I/O（不占用 API 默认 executor）。

    支持 kwargs 透传（如 since 参数）。
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        # run_in_executor 不支持 kwargs，用 functools.partial 包装
        import functools
        return await loop.run_in_executor(
            get_kline_collector_executor(),
            functools.partial(func, *args, **kwargs),
        )
    return await loop.run_in_executor(get_kline_collector_executor(), func, *args)


def shutdown_kline_collector_executor(*, wait: bool = False) -> None:
    """停止采集器时释放线程池。"""
    global _executor
    with _lock:
        if _executor is not None:
            _executor.shutdown(wait=wait, cancel_futures=not wait)
            _executor = None
