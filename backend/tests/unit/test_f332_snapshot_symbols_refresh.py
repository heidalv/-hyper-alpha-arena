# -*- coding: utf-8 -*-
"""[F332 2026-09-22] 快照符号集必须定期刷新，不能只取一次。

# 事故（实测）

`market_orderbook_snapshots` 里 30 个 asterdex 币，**只有 13 个还在更新**，
另外 17 个在 09-16 ~ 09-21 之间陆续停止：

    ONDO / PENDLE / SEI / 1000SHIB   停于 09-21 10:11
    ZEC                              停于 09-21 11:07
    ARB                              停于 09-21 09:48
    LINK                             停于 09-17 14:50
    AVAX / ADA                       停于 09-16 15:35

根因在 `_buffer_snapshot`：

    if not self._snap_symbols:                  # ← 只算一次
        self._snap_symbols = self._snapshot_symbols()

而 `_snapshot_symbols()` 取的是 `get_research_priority_symbols(limit=150)`
—— 那是**动态**优先清单（实测当前 17 个币，且会变）。冻结之后：
  · 新进清单的币**永远不落库**（在快照表里表现为"从未存在"）
  · 移出清单的币继续占用符号集

# 为什么这个 bug 比"少几个币"更糟

它让**所有基于快照表的判断都建立在冻结的清单上**。
实测后果：我曾看到该表"只有 12 个币"，据此断定"引擎只能用这 12 个币"，
并据此换了车道宇宙 —— 而当时真实活跃的是 13 个且随时在变。
**一个不刷新的缓存，会把一次性的观测变成永久的错误前提。**

# 本测试固定什么

1. 首次调用会算一次符号集
2. **TTL 内不重复计算**（避免每 10s 打一次优先清单）
3. **TTL 到期后必须重算**（回归核心）
4. 刷新用**并集**，不替换 —— 替换会在清单抖动时丢掉仍在正常落库的币，
   制造与本次事故同型的人为缺口
5. 真正决定落库的仍是"币在符号集里"这一层过滤
"""
from __future__ import annotations

import importlib.util
import io
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
POLLER = ROOT / "backend" / "services" / "asterdex_ticker_poller.py"


def _load():
    spec = importlib.util.spec_from_file_location("poller_under_test", POLLER)
    m = importlib.util.module_from_spec(spec)
    saved = sys.stdout, sys.stderr
    sink = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    try:
        sys.stdout = sys.stderr = sink
        spec.loader.exec_module(m)
    finally:
        sys.stdout, sys.stderr = saved
    return m


class _AliveThread:
    """冒充活着的 flusher（`_buffer_snapshot` 会先检查它）。"""

    def is_alive(self):
        return True


@pytest.fixture()
def poller(monkeypatch):
    m = _load()
    p = m.AsterdexTickerPoller() if hasattr(m, "AsterdexTickerPoller") else None
    if p is None:
        # 取模块里唯一的轮询器实例
        for name in dir(m):
            obj = getattr(m, name)
            if obj.__class__.__name__.endswith("TickerPoller"):
                p = obj
                break
    assert p is not None, "找不到 ticker poller 实例"
    p._snap_flusher = _AliveThread()
    p._snap_buffer = []
    p._snap_symbols = set()
    p._snap_symbols_at = 0.0
    p._snap_dropped = 0
    return p


def _install_fake_symbols(p, monkeypatch, seq: list):
    """把 `_snapshot_symbols` 换成按调用次序返回不同清单的假实现。"""
    calls = {"n": 0}

    def fake():
        i = min(calls["n"], len(seq) - 1)
        calls["n"] += 1
        return set(seq[i])

    monkeypatch.setattr(p, "_snapshot_symbols", fake)
    return calls


@pytest.mark.unit
def test_file_exists():
    assert POLLER.exists(), f"找不到 {POLLER}"


@pytest.mark.unit
def test_first_call_computes_symbols(poller, monkeypatch):
    calls = _install_fake_symbols(poller, monkeypatch, [{"AAA", "BBB"}])
    poller._buffer_snapshot({"AAA": (1.0, 0), "ZZZ": (2.0, 0)})
    assert calls["n"] == 1, "首次必须计算符号集"
    assert [r[0] for r in poller._snap_buffer] == ["AAA"], "只落符号集内的币"


@pytest.mark.unit
def test_within_ttl_does_not_recompute(poller, monkeypatch):
    """TTL 内不重算 —— 否则每 10s 打一次优先清单。"""
    monkeypatch.setenv("SNAP_SYMBOLS_TTL_SEC", "300")
    calls = _install_fake_symbols(poller, monkeypatch, [{"AAA"}])
    poller._buffer_snapshot({"AAA": (1.0, 0)})
    poller._buffer_snapshot({"AAA": (1.0, 0)})
    poller._buffer_snapshot({"AAA": (1.0, 0)})
    assert calls["n"] == 1, f"TTL 内不应重算，实际算了 {calls['n']} 次"


@pytest.mark.unit
def test_after_ttl_recomputes(poller, monkeypatch):
    """**核心回归**：TTL 到期后必须重算，新进清单的币要能开始落库。

    这正是 ONDO/ZEC 断流的原因 —— 冻结后新币永远进不来。
    """
    monkeypatch.setenv("SNAP_SYMBOLS_TTL_SEC", "1")
    calls = _install_fake_symbols(poller, monkeypatch, [{"AAA"}, {"AAA", "NEW"}])

    poller._buffer_snapshot({"AAA": (1.0, 0), "NEW": (3.0, 0)})
    assert "NEW" not in [r[0] for r in poller._snap_buffer], "首轮 NEW 还不在清单里"
    assert calls["n"] == 1

    time.sleep(1.2)
    poller._buffer_snapshot({"AAA": (1.0, 0), "NEW": (3.0, 0)})
    assert calls["n"] == 2, "TTL 到期必须重算"
    assert "NEW" in [r[0] for r in poller._snap_buffer], \
        "重算后新币必须开始落库（否则就是本次事故的重演）"


@pytest.mark.unit
def test_refresh_is_union_not_replace(poller, monkeypatch):
    """刷新必须是**并集**。

    替换的后果：优先清单抖动一下（某币临时掉出），
    它就在快照表里留下一个与真实断流无法区分的"人为缺口"。
    """
    monkeypatch.setenv("SNAP_SYMBOLS_TTL_SEC", "1")
    _install_fake_symbols(poller, monkeypatch, [{"KEEP", "GONE"}, {"KEEP"}])
    poller._buffer_snapshot({"KEEP": (1.0, 0), "GONE": (2.0, 0)})
    assert {"KEEP", "GONE"} <= poller._snap_symbols

    time.sleep(1.2)
    poller._buffer_snapshot({"KEEP": (1.0, 0), "GONE": (2.0, 0)})
    assert "GONE" in poller._snap_symbols, (
        "刷新不得把 GONE 剔掉 —— 替换式刷新会在清单抖动时制造人为缺口")


@pytest.mark.unit
def test_empty_fresh_does_not_clobber(poller, monkeypatch):
    """优先清单取空（依赖异常）时不得清空符号集。"""
    monkeypatch.setenv("SNAP_SYMBOLS_TTL_SEC", "1")
    _install_fake_symbols(poller, monkeypatch, [{"AAA"}, set()])
    poller._buffer_snapshot({"AAA": (1.0, 0)})
    time.sleep(1.2)
    poller._buffer_snapshot({"AAA": (1.0, 0)})
    assert "AAA" in poller._snap_symbols, "空清单不得清空符号集"


@pytest.mark.unit
def test_symbols_outside_set_never_written(poller, monkeypatch):
    """反向确认：不在符号集里的币永远不落库（过滤本身仍然有效）。"""
    _install_fake_symbols(poller, monkeypatch, [{"AAA"}])
    poller._buffer_snapshot({"AAA": (1.0, 0), "BBB": (9.0, 0), "CCC": (9.0, 0)})
    assert [r[0] for r in poller._snap_buffer] == ["AAA"]
