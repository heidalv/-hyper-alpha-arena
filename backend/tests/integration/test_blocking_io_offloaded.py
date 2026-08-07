# backend/tests/integration/test_blocking_io_offloaded.py
"""阶段5 Task 5.3:阻塞 I/O 移出事件循环 —— 并发性证明测试。

为什么需要这个测试 / Why
-------------------------
容量分析 §3.3.3 ② 担心路由处理器里的同步 ``requests.post`` 会卡住
单 worker 的事件循环,导致同 worker 上的其它请求被串行化。

但 **原始审计对代码状态的假设不准确**:被点名的几个路由文件
(``account_routes`` / ``hyperliquid_routes`` / ``llm_config_routes`` /
``smart_signal_routes``)里所有命中 ``requests.post`` 的处理器都是
**普通 ``def``**(不是 ``async def``)。

FastAPI/Starlette 对 ``def`` 处理器会自动 ``run_in_threadpool`` ->
``anyio.to_thread.run_sync``(默认线程池容量 40),即同步处理器
**本来就**跑在事件循环之外,慢请求不会阻塞同 worker 上的其它请求。

完整 backend/ 扫描后 ``async def`` 里直接命中阻塞 I/O 的只有两处,
且都已正确处理:
- ``backend/api/kline_analysis_routes.py:create_ai_analysis`` 已经用
  ``await asyncio.to_thread(analyze_kline_chart, ...)`` 包裹,工作早已移出循环。
- ``backend/api/system_control_routes.py:shutdown_services`` 里的
  ``time.sleep(1)`` 位于 ``delayed_shutdown()`` 内部,后者由独立的
  ``threading.Thread`` 启动,本身就不在事件循环上。

本测试用「两个慢请求并发 -> 总耗时接近单请求」的方式,**证明**
"同步处理器不会串行化同 worker 上的并发请求" 这一并发性属性,
避免后续重构(例如把同步处理器改成 ``async def``)不小心回退成
"卡住事件循环" 的反模式。

注意
----
测试不依赖真实网络,通过 mock 注入 0.5s 慢响应。
"""
from __future__ import annotations

import time
from concurrent.futures import Future
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── 用于证明属性的玩具 app ─────────────────────────────────────────
# 两个处理器都是 ``def``(不是 ``async def``),模拟当前代码库里
# ``requests.post`` 所在处理器的真实形态。处理器里直接调用阻塞函数
# ``blocking_call()``,如果 Starlette 没把它丢到线程池,两个并发请求
# 会串行(总耗时 ≈ 1.0s);如果丢到线程池(预期),总耗时 ≈ 0.5s。
def _build_toys() -> FastAPI:
    app = FastAPI()

    BLOCK_SECONDS = 0.5

    def blocking_call() -> float:
        # 模拟 requests.post 慢响应
        time.sleep(BLOCK_SECONDS)
        return BLOCK_SECONDS

    @app.get("/sync-handler")
    def sync_handler() -> dict:
        # 故意直接调用阻塞函数 —— 与现有 ``def`` 处理器内
        # 直接调 ``requests.post`` 的形态一致
        elapsed = blocking_call()
        return {"elapsed": elapsed}

    @app.get("/async-handler-to-thread")
    async def async_handler_to_thread() -> dict:
        # 对照组:async 处理器 + asyncio.to_thread(等价并发性)
        import asyncio
        elapsed = await asyncio.to_thread(blocking_call)
        return {"elapsed": elapsed}

    return app


def _measure_concurrent(client: TestClient, path: str, n: int = 2) -> float:
    """以线程并发发起 n 个请求,返回 wall-clock 总耗时。"""
    results: list = []
    futures: list[Future] = []

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=n) as ex:
        for _ in range(n):
            futures.append(ex.submit(lambda: client.get(path).status_code))
        for f in futures:
            results.append(f.result())
    return results


def test_sync_handler_does_not_serialize_concurrent_requests():
    """关键断言:两个 ``def`` 处理器并发执行时,总耗时 < 2x 单请求。

    若 Starlette 把同步处理器跑在事件循环上(错误假设),两个 0.5s
    请求会串行 -> 总耗时 ≈ 1.0s。实际跑在线程池 -> ≈ 0.5s。
    """
    app = _build_toys()
    with TestClient(app) as client:
        start = time.perf_counter()
        statuses = _measure_concurrent(client, "/sync-handler", n=2)
        elapsed = time.perf_counter() - start

    assert all(s == 200 for s in statuses), f"unexpected statuses: {statuses}"
    # 阈值取 1.5x 单请求(给线程/HTTP 开销留余量)。< 1.5 * 0.5 = 0.75s
    # 即证明不是串行(串行会是 ≈ 1.0s)。
    assert elapsed < 0.75, (
        f"sync handler looks serialized: elapsed={elapsed:.3f}s "
        f"(expected parallel ≈0.5s, serialized would be ≈1.0s)"
    )


def test_async_handler_with_to_thread_is_concurrent():
    """对照组:``async def`` + ``asyncio.to_thread`` 同样并发。"""
    app = _build_toys()
    with TestClient(app) as client:
        start = time.perf_counter()
        statuses = _measure_concurrent(client, "/async-handler-to-thread", n=2)
        elapsed = time.perf_counter() - start

    assert all(s == 200 for s in statuses), f"unexpected statuses: {statuses}"
    assert elapsed < 0.75, (
        f"async+to_thread handler looks serialized: elapsed={elapsed:.3f}s"
    )


# ── 真实代码盘点断言 ───────────────────────────────────────────────
# 防止后续把当前 ``def`` 处理器误改成 ``async def`` 又不 wrap,从而
# 引入"卡事件循环"的回归。如果某个目标处理器变成 ``async def``,
# 下列断言会失败,提醒维护者必须包 ``asyncio.to_thread``。
def _first_line_of_function(source: str, func_name: str) -> str:
    """从源码里抓 ``def NAME`` / ``async def NAME`` 行(粗略匹配)。"""
    import re
    pat = re.compile(rf"^(async\s+)?def\s+{re.escape(func_name)}\b", re.MULTILINE)
    m = pat.search(source)
    return m.group(0) if m else ""


_TARGETS = [
    # (file, handler, expected_prefix) — 这些处理器当前都是同步 ``def``
    ("backend/api/account_routes.py", "test_llm_connection", "def"),
    ("backend/api/account_routes.py", "check_builder_authorization", "def"),
    ("backend/api/llm_config_routes.py", "test_llm_config", "def"),
    ("backend/api/smart_signal_routes.py", "ai_deep_analysis", "def"),
    ("backend/api/hyperliquid_routes.py", "configure_account_wallet", "def"),
]


@pytest.mark.parametrize("rel_path,func_name,expected_prefix", _TARGETS)
def test_named_handlers_remain_off_event_loop(rel_path, func_name, expected_prefix):
    """断言被点名的处理器仍是 ``def``,即自动跑在线程池里。

    一旦有人把它改成 ``async def`` 且 body 里仍直接调 ``requests.post``,
    本测试就会失败 —— 提醒要么保持 ``def``,要么用 ``await asyncio.to_thread(...)``。
    """
    import os
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    path = os.path.join(repo_root, rel_path.replace("/", os.sep))
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    line = _first_line_of_function(src, func_name)
    assert line, f"{func_name} not found in {rel_path}"
    assert line.startswith(expected_prefix), (
        f"{rel_path}::{func_name} 形态变了:{line!r}。"
        f"如果改成 async def,body 里直接调 requests.post 会卡住事件循环 —— "
        f"请保持 def,或用 await asyncio.to_thread(...) 包裹阻塞调用。"
    )
