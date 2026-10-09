# -*- coding: utf-8 -*-
"""[F331 2026-09-21] `market_data_center` 端口占用必须明确退出，不能变僵尸。

# 事故

审计（`scripts/h199_data_audit.py`）发现**两个** `market_data_center` 进程：

    pid 40764  backend\\.venv\\Scripts\\python.exe   1 线程 / 4MB / **CPU 零增长**
    pid 28752  .runtime\\Python312\\python.exe       95 线程 / 1.8GB / CPU 在动

前者是**僵尸**：`main()` 第 665 行先 `_start_health_server(port)`，
端口被占时抛 `OSError: [Errno 10048]`，异常从 `main()` 冒出
⇒ 进程**没进事件循环**，但在 `InteractiveToken` 计划任务下**没有干净退出**。

# 危害（不是双写）

实测那个僵尸**一行都没写过**。真正的问题是**可观测性被污染**：
  · 巡检看到进程存在就以为数据中心在跑，而实际采集未必健康
  · 它占着任务槽位（`MultipleInstancesPolicy=IgnoreNew`）
  · 它让"有几个数据中心实例"这个问题无法回答

# 本测试固定什么

1. 端口被占 ⇒ `_start_health_server` 抛 `SystemExit`（退出码 3），不是 `OSError`
2. 端口空闲 ⇒ 正常启动
3. 源码层面：`main()` 里**先起 health server**，所以端口检查天然是单实例闸
   （不需要额外锁）—— 但必须是**明确失败**才成立
"""
from __future__ import annotations

import importlib.util
import io
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DC = ROOT / "backend" / "workers" / "market_data_center.py"


def _load():
    """加载模块（隔离 stdout/stderr 重绑，避免 pytest 捕获流被关掉 —— F294 的教训）。"""
    spec = importlib.util.spec_from_file_location("dc_under_test", DC)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout, sys.stderr
    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        sys.stdout = sys.stderr = sink
        spec.loader.exec_module(m)
    finally:
        sys.stdout, sys.stderr = saved
    return m


@pytest.fixture()
def dc():
    return _load()


@pytest.mark.unit
def test_module_exists():
    assert DC.exists(), f"找不到 {DC}"


@pytest.mark.unit
def test_free_port_starts_ok(dc):
    """端口空闲 ⇒ 正常启动并返回 server，随后关掉。"""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    httpd = dc._start_health_server(port)
    try:
        assert httpd is not None
    finally:
        httpd.shutdown()
        httpd.server_close()


@pytest.mark.unit
def test_busy_port_exits_cleanly(dc):
    """**核心**：端口被占 ⇒ 必须 `SystemExit`（带退出码），不能是裸 OSError。

    裸 OSError 会从 `main()` 冒出去，在计划任务下留下"起得来、不干活、
    也看不出死了"的僵尸进程 —— 那正是本次事故。

    ⚠️ 占位 socket **不能设 `SO_REUSEADDR`**：
    在 Windows 上该选项允许**端口劫持**（第二个 socket 也能绑上同一端口），
    于是"端口被占"这个前提根本不成立，测试会假通过/假失败。
    首版就是设了它 ⇒ `DID NOT RAISE SystemExit`。
    同时也必须绑 `0.0.0.0`（与生产的 `_start_health_server` 同地址），
    否则 127.0.0.1 与 0.0.0.0 可能互不冲突。
    """
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        with pytest.raises(SystemExit) as ei:
            dc._start_health_server(port)
        assert ei.value.code == 3, f"退出码应为 3，实际 {ei.value.code}"
    finally:
        s.close()


@pytest.mark.unit
def test_busy_port_does_not_raise_oserror(dc):
    """反向确认：不能再漏出 OSError（否则又回到僵尸路径）。"""
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    port = s.getsockname()[1]
    s.listen(1)
    try:
        try:
            dc._start_health_server(port)
        except SystemExit:
            pass
        except OSError as e:  # pragma: no cover
            pytest.fail(f"仍抛 OSError ⇒ 会变僵尸：{e}")
    finally:
        s.close()


@pytest.mark.unit
def test_health_server_is_started_before_collectors():
    """源码顺序断言：health server 在 `_run_collectors` **之前**启动。

    这正是它天然充当单实例闸的原因 —— 若哪天有人把顺序调换，
    端口冲突就不再能阻止第二个实例，本测试要能拦住。
    """
    src = DC.read_text(encoding="utf-8")
    i_srv = src.find("httpd = _start_health_server(port)")
    i_run = src.find("loop.run_until_complete(_run_collectors(stop))")
    assert i_srv > 0, "找不到 _start_health_server(port) 调用点"
    assert i_run > 0, "找不到 _run_collectors 调用点"
    assert i_srv < i_run, (
        "health server 必须在采集器之前启动 —— 否则端口冲突不再阻止第二个实例，"
        "会出现两个数据中心同时采集")
