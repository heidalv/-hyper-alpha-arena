# -*- coding: utf-8 -*-
"""[2026-09-18 前端刷新慢] GIL/排队可观测层 —— 让"端点慢"可区分「查询慢」与「排队久」。

## 根因（实证，详见 docs/前端刷新慢根因_20260918.md）
后端单进程 GIL 上限约 1 核，而进程自己的后台循环在**零请求**时也已中位占用 ~99% 单核
（12 个空载窗：中位 99%、最低 19%、最高 138%）。所有同步 `def` 端点排队等 GIL：

| 对照 | 结果 |
|---|---|
| async `/health` 串行→并发20 | 27ms → 61ms（×2.3） |
| 同步 `orders` 串行→并发20 | 363ms → 3593ms（×9.9） |
| `orders` 高 CPU(>60%) vs 低 CPU | 中位 708ms vs 184ms（3.8×） |

此前只有 `SLOW ≥3s` 一条事后线索 ⇒ 无法区分两者，本次定位耗时 3 小时。本文件锁住这层观测：

1. 计数正确（在飞/峰值/窗口/超阈）；
2. 口径写明（cpu_pct = **进程内所有线程合计** CPU / 墙钟；阈值与 SLOW 同口径）；
3. **挂在每请求必经路径上，任何输入都不得抛异常**（失败只累加 errors）；
4. 可关（`GIL_WATCH_INTERVAL_S=0`）、可查（`GET /api/ops/gil-watch`）、接线真实存在。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services import gil_watch  # noqa: E402


@pytest.fixture(autouse=True)
def _clean():
    gil_watch.reset_for_tests()
    yield
    gil_watch.stop()
    time.sleep(0.05)
    gil_watch.reset_for_tests()


# ─────────────────────── 1. 计数正确 ───────────────────────

def test_inflight_and_totals():
    gil_watch.request_started()
    gil_watch.request_started()
    gil_watch.request_finished(0.05)
    snap = gil_watch.snapshot()
    assert snap["requests_total"] == 2
    assert snap["inflight_now"] == 1
    assert snap["inflight_peak_current_window"] == 2
    assert snap["over_1s_total"] == 0 and snap["over_3s_total"] == 0


def test_over_thresholds_match_slow_log_semantics():
    """阈值计数口径与 SLOW 日志一致：≥1s 与 ≥3s，按**请求墙钟**。"""
    for d in (0.01, 0.5, 1.0, 2.9, 3.0, 10.0):
        gil_watch.request_started()
        gil_watch.request_finished(d)
    snap = gil_watch.snapshot()
    assert snap["over_1s_total"] == 4, "≥1s 的是 1.0 / 2.9 / 3.0 / 10.0（0.01、0.5 不算）"
    assert snap["over_3s_total"] == 2, "≥3s 的是 3.0 / 10.0"


def test_finished_without_started_never_goes_negative():
    gil_watch.request_finished(0.1)
    gil_watch.request_finished(0.1)
    assert gil_watch.snapshot()["inflight_now"] == 0


# ─────────────────────── 2. 窗口滚动与口径 ───────────────────────

def test_roll_produces_window_and_clears_samples():
    for d in (0.02, 0.30, 1.50):
        gil_watch.request_started()
        gil_watch.request_finished(d)
    win = gil_watch._state.roll()
    assert win["requests"] == 3
    assert win["latency_max_ms"] == pytest.approx(1500.0, abs=1.0)
    assert win["latency_median_ms"] == pytest.approx(300.0, abs=1.0)
    assert win["over_1s"] == 1 and win["over_3s"] == 0
    assert win["cpu_pct"] is not None and win["cpu_pct"] >= 0
    assert win["threads"] and win["threads"] >= 1
    # 滚动后窗口清空，但累计值保留
    snap = gil_watch.snapshot()
    assert snap["last_window"] is not None
    assert snap["current_window_samples"] == 0
    assert snap["requests_total"] == 3


def test_snapshot_documents_its_metric_semantics():
    """口径必须随数据一起可读（禁止"读数含义靠猜"）。"""
    snap = gil_watch.snapshot()
    assert "进程内所有线程合计" in snap["note"]
    assert "GIL" in snap["note"]
    for k in ("enabled", "interval_s", "requests_total", "inflight_now",
              "process_cpu_seconds_total", "last_window", "errors"):
        assert k in snap, f"快照缺字段 {k}"


# ─────────────────────── 3. 绝不抛异常（挂在每请求路径上） ───────────────────────

def test_never_raises_on_bad_input():
    gil_watch.request_finished("不是数字")     # 类型错误
    gil_watch.request_finished(None)
    gil_watch.request_started()
    snap = gil_watch.snapshot()
    assert snap["errors"] >= 2, "异常应被吞掉并计入 errors（禁止静默无痕）"
    assert snap["inflight_now"] == 1, "一次成功的 started 仍应计入"


def test_request_hooks_do_not_raise_even_if_state_broken(monkeypatch):
    class _Boom:
        def started(self):
            raise RuntimeError("boom")

        def finished(self, _e):
            raise RuntimeError("boom")

    monkeypatch.setattr(gil_watch, "_state", _Boom())
    gil_watch.request_started()          # 不得抛
    gil_watch.request_finished(1.0)      # 不得抛


# ─────────────────────── 4. 开关与线程 ───────────────────────

def test_start_disabled_returns_false_without_thread(monkeypatch):
    monkeypatch.setenv("GIL_WATCH_INTERVAL_S", "0")
    assert gil_watch.start() is False
    assert gil_watch.snapshot()["enabled"] is False


def test_start_enabled_spawns_and_logs(caplog, monkeypatch):
    monkeypatch.setenv("GIL_WATCH_INTERVAL_S", "0.05")
    with caplog.at_level("INFO", logger="backend.services.gil_watch"):
        assert gil_watch.start() is True
        gil_watch.request_started()
        gil_watch.request_finished(0.01)
        # 注意：启动行也含 [GILWatch]，必须等**窗口行**（进程CPU=）而不是等标记本身
        deadline = time.time() + 5.0
        while time.time() < deadline and "进程CPU=" not in caplog.text:
            time.sleep(0.05)
    assert "进程CPU=" in caplog.text, "窗口行未上报（观测层静默失效）"
    assert "在飞峰值=" in caplog.text and "≥3s=" in caplog.text
    gil_watch.stop()


def test_start_is_idempotent(monkeypatch):
    monkeypatch.setenv("GIL_WATCH_INTERVAL_S", "5")
    assert gil_watch.start() is True
    t1 = gil_watch._thread
    assert gil_watch.start() is True
    assert gil_watch._thread is t1, "重复调用不得再起一个采样线程"
    gil_watch.stop()


# ─────────────────────── 5. 接线真实存在（防"改了没接"） ───────────────────────

def test_middleware_records_every_request():
    src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    start = src.index("async def _slow_request_profiler")
    body = src[start:start + 1200]
    assert "gil_watch" in body, "慢请求中间件未接 gil_watch"
    assert "request_started()" in body and "request_finished(" in body, \
        "计数必须同时有开始与结束（只记一头会永远漏）"
    assert "call_next(request)" in body


def test_started_on_app_startup():
    src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    start = src.index('@app.on_event("startup")')
    seg = src[start:start + 4000]
    assert "gil_watch" in seg and "_gw.start()" in seg, "启动钩子未拉起观测线程"


def test_ops_endpoint_exposes_snapshot():
    src = (ROOT / "backend" / "api" / "ops_routes.py").read_text(encoding="utf-8")
    assert '@router.get("/gil-watch")' in src, "缺少 /api/ops/gil-watch 查询端点"
    assert "gil_watch.snapshot()" in src

    # 直接调用路由函数（不经 HTTP），确认返回体可用
    from backend.api.ops_routes import ops_gil_watch
    body = ops_gil_watch()
    assert isinstance(body, dict) and "note" in body and "last_window" in body
