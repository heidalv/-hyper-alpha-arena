# -*- coding: utf-8 -*-
"""GIL/排队可观测：把"请求被自己的后台负载连累"变成可查指标。

## 为什么要这个（2026-09-18 前端刷新慢取证）
后端是**单进程**，GIL 上限约 1 核；而进程自己的后台循环（交易循环 / 因子路线 /
行情流 / whale / 快照采集…）在**一个请求都不发**时也已中位占用 ≈99% 单核
（12 个空载窗实测：中位 99%、最低 19%、最高 138%；12 分钟监视中位 102%、67% 样本 >80%）。

所有同步 `def` 端点都要排队等 GIL，于是**同一端点**可以 25ms，也可以 4.6s：
- 串行 vs 并发 20：async `/health` ×2.3，同步 `orders` ×9.9（363ms → 3593ms）；
- 高 CPU(>60%) 时 `orders` 中位 708ms，低 CPU(≤60%) 时 184ms（3.8×）。

此前只有 `SLOW ≥3s` 一条事后线索，**无法区分"查询慢"与"排队久"**，本次定位花了 3 小时。
本模块补上缺的那一层：窗口内的**在飞请求数**（并发压力）+ **进程 CPU%**（GIL 占用强度代理）
+ 请求耗时分位/超阈计数，每 interval 打一行，并可由 `GET /api/ops/gil-watch` 直接读。

## 口径（必须写明，避免误读）
- `cpu_pct` = 窗口内 `time.process_time()` 增量 / 墙钟增量 × 100
  ⇒ **进程内所有线程合计**的 CPU 占用；在 CPython 里它长期贴着 100%（GIL 上限）。
  它**不是**机器整体 CPU，也**不是**请求自身的 CPU。
- `inflight_peak` = 窗口内同时未返回的 HTTP 请求数峰值（含所有端点）。
- 阈值计数按**请求墙钟**计（与 `SLOW` 日志同口径）。
- 关闭：`GIL_WATCH_INTERVAL_S=0`。此时 `snapshot()` 仍可读累计值，但不再打日志。

## 纪律
本模块的任何函数**都不得抛异常**（它挂在每个请求的必经路径上）——
所有入口都自带兜底；失败时静默降级为"只计数、不上报"，并有 `errors` 字段可查。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_INTERVAL_ENV = "GIL_WATCH_INTERVAL_S"


def _interval_default() -> float:
    raw = (os.getenv(_INTERVAL_ENV) or "").strip()
    if not raw:
        return 60.0
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 60.0


class _State:
    """线程安全计数器（HTTP 处理器在事件循环线程，采样器在后台线程）。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.inflight = 0
        self.inflight_peak = 0
        self.window: List[float] = []          # 本窗口请求耗时（秒）
        self.last_window: Optional[Dict[str, Any]] = None
        self.started_at = time.time()
        self.errors = 0
        self.total_requests = 0
        self.total_over_1s = 0
        self.total_over_3s = 0
        # CPU 基线（进程 CPU 秒）
        self._cpu_last = time.process_time()
        self._cpu_last_at = time.perf_counter()

    # ── 请求生命周期（每请求两次调用，必须极轻） ──
    def started(self) -> None:
        with self._lock:
            self.inflight += 1
            self.total_requests += 1
            if self.inflight > self.inflight_peak:
                self.inflight_peak = self.inflight

    def finished(self, elapsed: float) -> None:
        with self._lock:
            if self.inflight > 0:
                self.inflight -= 1
            self.window.append(float(elapsed))
            if elapsed >= 1.0:
                self.total_over_1s += 1
            if elapsed >= 3.0:
                self.total_over_3s += 1

    # ── 窗口滚动 ──
    def roll(self) -> Dict[str, Any]:
        with self._lock:
            samples = self.window
            self.window = []
            peak = self.inflight_peak
            self.inflight_peak = self.inflight
            inflight_now = self.inflight
            cpu_now = time.process_time()
            at_now = time.perf_counter()
            d_cpu = cpu_now - self._cpu_last
            d_wall = at_now - self._cpu_last_at
            self._cpu_last, self._cpu_last_at = cpu_now, at_now

            samples_sorted = sorted(samples)
            n = len(samples_sorted)
            med = samples_sorted[n // 2] if n else 0.0
            p95 = samples_sorted[min(n - 1, int(n * 0.95))] if n else 0.0
            win = {
                "seconds": round(d_wall, 3),
                "requests": n,
                "inflight_now": inflight_now,
                "inflight_peak": peak,
                "cpu_pct": round(d_cpu / d_wall * 100, 1) if d_wall > 0 else None,
                "latency_median_ms": round(med * 1000, 1),
                "latency_p95_ms": round(p95 * 1000, 1),
                "latency_max_ms": round((samples_sorted[-1] * 1000) if n else 0.0, 1),
                "over_1s": sum(1 for s in samples_sorted if s >= 1.0),
                "over_3s": sum(1 for s in samples_sorted if s >= 3.0),
                "at": time.strftime("%H:%M:%S"),
                "threads": _thread_count(),
            }
            self.last_window = win
        return win


_state = _State()
_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_interval = 0.0


def _thread_count() -> Optional[int]:
    try:
        return threading.active_count()
    except Exception:  # noqa: BLE001
        return None


def _bump_errors() -> None:
    """兜底计数：state 本身损坏时也绝不再抛（本模块挂在每请求必经路径上）。"""
    try:
        _state.errors += 1
    except Exception:  # noqa: BLE001
        pass


# ── 对外：请求生命周期（绝不抛） ──
def request_started() -> None:
    try:
        _state.started()
    except Exception:  # noqa: BLE001
        _bump_errors()


def request_finished(elapsed: float, path: str = "") -> None:
    try:
        _state.finished(elapsed)
    except Exception:  # noqa: BLE001
        _bump_errors()


# ── 采样线程 ──
def _loop(interval: float) -> None:
    while not _stop.wait(interval):
        try:
            win = _state.roll()
            logger.info(
                "[GILWatch] %gs 窗口: 请求=%d 在飞峰值=%d 进程CPU=%s%% 中位=%.0fms "
                "最大=%.0fms ≥1s=%d ≥3s=%d 线程=%s",
                win["seconds"], win["requests"], win["inflight_peak"],
                win["cpu_pct"], win["latency_median_ms"], win["latency_max_ms"],
                win["over_1s"], win["over_3s"], win["threads"],
            )
        except Exception:  # noqa: BLE001
            _bump_errors()


def start(interval: Optional[float] = None) -> bool:
    """启动采样线程；返回是否已启动（幂等）。interval<=0 表示关闭。"""
    global _thread, _interval
    try:
        iv = _interval_default() if interval is None else float(interval)
    except Exception:  # noqa: BLE001
        iv = 60.0
    _interval = iv
    if iv <= 0:
        logger.info("[GILWatch] 已关闭（%s=0）", _INTERVAL_ENV)
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _stop.clear()
    _thread = threading.Thread(target=_loop, args=(iv,), daemon=True, name="gil-watch")
    _thread.start()
    logger.info("[GILWatch] 已启动：每 %gs 上报一次（%s 可调，0=关闭）", iv, _INTERVAL_ENV)
    return True


def stop() -> None:
    _stop.set()


def snapshot() -> Dict[str, Any]:
    """当前累计快照（只读；供 `GET /api/ops/gil-watch`）。"""
    with _state._lock:  # noqa: SLF001 — 同模块内部状态
        cur_window = list(_state.window)
        inflight = _state.inflight
        peak = _state.inflight_peak
        total = _state.total_requests
        o1, o3 = _state.total_over_1s, _state.total_over_3s
    now = time.process_time()
    cpu_total = now  # 进程启动至今的 CPU 秒
    return {
        "enabled": bool(_interval > 0),
        "interval_s": _interval,
        "uptime_s": round(time.time() - _state.started_at, 1),
        "requests_total": total,
        "over_1s_total": o1,
        "over_3s_total": o3,
        "inflight_now": inflight,
        "inflight_peak_current_window": peak,
        "current_window_samples": len(cur_window),
        "process_cpu_seconds_total": round(cpu_total, 1),
        "last_window": _state.last_window,
        "errors": _state.errors,
        "note": ("cpu_pct = 进程内所有线程合计 CPU / 墙钟（CPython 长期贴 100% = GIL 饱和）；"
                 "阈值计数与 SLOW 日志同口径（请求墙钟）"),
    }


def reset_for_tests() -> None:
    """仅测试用：清空计数器与窗口。"""
    global _state, _interval
    _state = _State()
    _interval = 0.0
