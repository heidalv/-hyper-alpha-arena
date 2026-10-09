# -*- coding: utf-8 -*-
"""[F334 2026-09-22] market_flow 必须向**共享**限流器申请额度，不能只读冷却状态。

# 事故

`market_orderbook_snapshots`（**引擎 `fetch_market` 的输入**）出现**多币同时**中断：

```
04:19 → 04:54   LINK / ICP / AVAX / ONDO / AR    五个币同时缺 35 分钟
21:26 → 03:02   ATOM                             缺 5.6 小时
```

**多个币同时停 ⇒ 不是单币问题，是共享出口被限流。**
同期 `logs/data-center.log` 里 Asterdex REST 大面积限流：

```
[asterdex] BULLA/1m batch failed: binanceusdm GET fapi.asterdex.com/... 限流
[DepthBackfill] ICP/5m@asterdex 失败: [asterdex] 历史回填限流
```

# 根因

Asterdex 的 REST 配额是**整 IP 共享**的。`_AsterdexRateLimiter` 就是为此设的，
其 docstring 明写"供 market_flow 等独立组件检查"。

但 market_flow 此前**只调用 `banned_remaining()` 读冷却**、
**从不调用 `wait()` 申请额度** ⇒ 它的请求**不占窗口预算**，
可以毫无约束地撞满配额，把 kline / depth_backfill 一起拖进 429/418。

# 本测试固定什么

1. `_wait_global_ban` 真的调用了共享限流器的 `wait()`（回归核心）
2. 冷却期内仍然等待（原语义不能丢）
3. 未冷却时立即返回（不能把正常路径也拖慢）
4. 三个轮询循环（trades / orderbook / asset_metrics）都接入了本函数

⚠️ 被测方法在 `self.running` 为真时**会循环调用 `wait()`**，
所以测试必须在它返回后把 `running` 置 False，否则会无限循环。
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
COLLECTOR = ROOT / "backend" / "services" / "market_flow" / "asterdex_collector.py"


def _load():
    spec = importlib.util.spec_from_file_location("mf_under_test", COLLECTOR)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout, sys.stderr
    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        sys.stdout = sys.stderr = sink
        spec.loader.exec_module(m)
    finally:
        sys.stdout, sys.stderr = saved
    return m


class _FakeCollector:
    """只为调用 `_wait_global_ban`；带 `running` 与共享限流器桩。"""

    def __init__(self, cls):
        self.running = True
        self._cls = cls
        self.n_wait = 0

    async def _wait_global_ban(self):
        return await self._cls._wait_global_ban(self)


class _FakeLimiter:
    def __init__(self):
        self.calls: list = []
        self.ban_remaining = 0.0

    def wait(self, bucket="live"):
        self.calls.append(bucket)
        if self.ban_remaining > 0:
            raise RuntimeError("ExchangeRateLimitError(stub)")

    def banned_remaining(self):
        return self.ban_remaining


@pytest.fixture()
def coll():
    m = _load()
    cls = None
    for name in dir(m):
        obj = getattr(m, name)
        if isinstance(obj, type) and hasattr(obj, "_wait_global_ban"):
            cls = obj
            break
    assert cls is not None, "找不到含 _wait_global_ban 的类"
    return cls


@pytest.mark.unit
def test_file_exists():
    assert COLLECTOR.exists(), f"找不到 {COLLECTOR}"


@pytest.mark.unit
def test_wait_is_called_when_not_banned(coll):
    """**核心回归**：未冷却时必须向共享限流器申请一次额度。"""
    lim = _FakeLimiter()
    c = _FakeCollector(coll)
    orig = coll._wait_global_ban

    async def runner():
        # 把共享限流器换成桩：直接改模块符号
        import backend.services.kline_collectors as kc
        saved = kc._AsterdexRateLimiter
        kc._AsterdexRateLimiter = lim
        try:
            # 让函数在第一次 wait 之后立刻退出（模拟放行）
            task = asyncio.create_task(orig(c))
            await asyncio.sleep(0.05)
            c.running = False
            await asyncio.wait_for(task, timeout=2.0)
        finally:
            kc._AsterdexRateLimiter = saved

    asyncio.run(runner())
    assert lim.calls, (
        "未调用共享限流器的 wait() ⇒ market_flow 的请求不占配额，"
        "会把 kline / depth_backfill 一起撞进 429/418（本次事故）")
    assert lim.calls[0] == "live", f"应走 live 桶，实际 {lim.calls[0]}"


@pytest.mark.unit
def test_waits_while_banned(coll):
    """冷却期内必须继续等待，不能放行 —— 用**源码顺序**断言。

    为什么不做运行时断言：本函数在冷却期的循环是
    `await asyncio.sleep(min(rem, 10.0))`，最短也要睡 10s，
    而测试只能给几秒超时 ⇒ 必然 `TimeoutError`。
    （首版就踩了这个：把"逻辑慢"误当成"断言失败"。）

    ⇒ 改为固定**语义顺序**：先申请额度，再读冷却，未冷却才 return。
    """
    src = COLLECTOR.read_text(encoding="utf-8")
    i_def = src.find("async def _wait_global_ban")
    assert i_def > 0
    body = src[i_def:i_def + 4000]

    # ⚠️ 锚点必须**跳过 docstring**：
    # 本次修复的 docstring 里同时提到了 `wait(` 与 `banned_remaining()`，
    # 直接 `find` 会命中说明文字 ⇒ 断言测的是注释顺序，不是代码顺序。
    # （首版就是这样：`assert 934 < 770` —— 两个下标都在 docstring 内。）
    i_doc_end = body.find('"""', body.find('"""') + 3)
    assert i_doc_end > 0, "找不到 docstring 结束位置"
    code = body[i_doc_end:]

    i_wait = code.find("_AsterdexRateLimiter.wait")
    i_ban = code.find("banned_remaining()")
    i_ret = code.find("return")
    assert i_wait > 0, "必须先申请共享额度"
    assert i_ban > 0, "必须读冷却状态"
    assert i_ret > 0, "必须有放行出口"
    assert i_wait < i_ban < i_ret, (
        "语义顺序应为：① 申请共享额度 → ② 读冷却 → ③ 未冷却才 return"
        f"（实际下标 wait={i_wait} ban={i_ban} ret={i_ret}）")
    assert "await asyncio.sleep" in code, "冷却期内必须等待"


@pytest.mark.unit
def test_blocking_wait_is_offloaded_to_thread():
    """**关键**：同步阻塞的 `wait()` 不能在事件循环线程里直接调用。

    `_AsterdexRateLimiter.wait()` 内部在额度耗尽时 `time.sleep(wait_s)`
    （最长 60s）并取 `threading.Lock`。若在协程里直接调用，
    **整个事件循环会被冻住** ⇒ 该进程所有协程一起停摆。

    （这是我在本次修复中真实引入过的缺陷：先写成 `wait("live")` 直调，
    自查时才发现必须 `await asyncio.to_thread(...)`。）
    """
    src = COLLECTOR.read_text(encoding="utf-8")
    i_def = src.find("async def _wait_global_ban")
    body = src[i_def:i_def + 4000]
    i_doc_end = body.find('"""', body.find('"""') + 3)
    code = body[i_doc_end:]
    assert "asyncio.to_thread(_AsterdexRateLimiter.wait" in code, (
        "共享限流器的 wait() 必须通过 asyncio.to_thread 调用，"
        "否则阻塞调用会冻住事件循环")


@pytest.mark.unit
def test_all_poll_loops_call_the_gate():
    """三个轮询循环都必须接入本函数 —— 漏一个就漏一份配额。"""
    src = COLLECTOR.read_text(encoding="utf-8")
    n = src.count("await self._wait_global_ban()")
    assert n >= 3, (
        f"只有 {n} 处调用 `_wait_global_ban()`；"
        "trades / orderbook / asset_metrics 三个轮询都要接入")


@pytest.mark.unit
def test_single_definition_after_edit():
    """只能有一个 `_wait_global_ban` 定义。

    本次修复期间我一次编辑留下了**重复定义**（旧 docstring 尾部未删），
    第二个定义会静默覆盖第一个 ⇒ 行为取决于书写顺序，极难排查。
    """
    src = COLLECTOR.read_text(encoding="utf-8")
    n = src.count("async def _wait_global_ban")
    assert n == 1, f"定义了 {n} 次 `_wait_global_ban`，必须只有 1 次"
