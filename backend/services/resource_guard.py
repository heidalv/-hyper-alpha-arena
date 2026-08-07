"""
G4 资源隔离守卫 — 训练与实盘热路径强制分离。

原则（§10.5 G4）：
  - lightgbm / torch / DSPy 等重计算禁止在 scalp/unified 热路径同步执行
  - 维护周期离峰线程异步跑训练
  - 热路径内调用 guard_training_operation 将 defer 或拒绝

零风险：RESOURCE_GUARD_ENABLED=false 时 no-op 放行。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Dict, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

_hot_depth = threading.local()
_stats_lock = threading.Lock()
_stats: Dict[str, int] = {
    "hot_entries": 0,
    "blocked_sync_train": 0,
    "deferred_off_peak": 0,
    "off_peak_runs": 0,
}

_HEAVY_PREFIXES = (
    "lightgbm", "torch", "tensorflow", "dspy", "sklearn.ensemble",
    "optuna", "sentence_transformers",
)


def is_enabled() -> bool:
    return os.environ.get("RESOURCE_GUARD_ENABLED", "true").strip().lower() in (
        "1", "true", "yes", "on",
    )


def get_guard_stats() -> Dict[str, int]:
    with _stats_lock:
        return dict(_stats)


def _bump(key: str, n: int = 1) -> None:
    with _stats_lock:
        _stats[key] = _stats.get(key, 0) + n


def hot_path_depth() -> int:
    return int(getattr(_hot_depth, "depth", 0) or 0)


def is_on_hot_path() -> bool:
    return hot_path_depth() > 0


@contextmanager
def hot_path_context(label: str = "trading"):
    """标记进入交易热路径（scalp/unified tick）。"""
    prev = hot_path_depth()
    _hot_depth_set(prev + 1)
    _bump("hot_entries")
    try:
        yield
    finally:
        _hot_depth_set(prev)


def _hot_depth_set(v: int) -> None:
    _hot_depth.depth = v


def guard_training_operation(op_name: str = "train") -> bool:
    """热路径上调用重训练时返回 False（应 defer 到离峰线程）。"""
    if not is_enabled():
        return True
    if is_on_hot_path():
        _bump("blocked_sync_train")
        logger.debug("[ResourceGuard] 热路径阻止同步训练: %s depth=%d", op_name, hot_path_depth())
        return False
    return True


def assert_off_hot_path(op_name: str = "heavy_compute") -> None:
    if is_enabled() and is_on_hot_path():
        _bump("blocked_sync_train")
        raise RuntimeError(f"[ResourceGuard] {op_name} 禁止在热路径执行 (depth={hot_path_depth()})")


def run_off_peak(
    fn: Callable[[], T],
    *,
    name: str = "off-peak-worker",
    debounce_sec: float = 0,
    last_run_holder: Optional[list] = None,
) -> Dict[str, Any]:
    """离峰异步执行重计算（与 ml_activation 同模式）。"""
    if debounce_sec > 0 and last_run_holder is not None:
        last = float(last_run_holder[0] if last_run_holder else 0)
        if (time.monotonic() - last) < debounce_sec:
            return {"ok": True, "skipped": True, "reason": "debounce"}

    def _worker():
        _bump("off_peak_runs")
        try:
            fn()
        except Exception as exc:
            logger.warning("[ResourceGuard] 离峰任务 %s 异常: %s", name, exc)

    if not guard_training_operation(name):
        _bump("deferred_off_peak")
        threading.Thread(target=_worker, daemon=True, name=name).start()
        if last_run_holder is not None:
            last_run_holder[:] = [time.monotonic()]
        return {"ok": True, "started": True, "deferred": True}

    result = fn()
    if last_run_holder is not None:
        last_run_holder[:] = [time.monotonic()]
    return {"ok": True, "started": False, "result": result}


def wrap_heavy_import(module_name: str) -> None:
    """检测是否在热路径 import 重模块（仅日志警告）。"""
    if not is_enabled() or not is_on_hot_path():
        return
    low = module_name.lower()
    if any(low.startswith(p) or p in low for p in _HEAVY_PREFIXES):
        logger.warning("[ResourceGuard] 热路径 import 重模块: %s", module_name)
