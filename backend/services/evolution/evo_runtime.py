"""因子进化运行时状态（供 Ops / compute status 轮询）。

定时 cron 与手动触发共用同一套状态，避免「后台在跑但面板显示空闲」。

[2026-08-27 单飞锁分周期] mark_start 从全局单飞改为按 period 单飞：
  - 同一 period 并发只允许 1 个（5m 不再被 4h 长轮次吞掉）；
  - 全局并发上限 FACTOR_EVO_MAX_CONCURRENT（默认 2，1~6）保留 CPU 预算约束；
  - 注：cron 走 FACTOR_EVO_SUBPROCESS=1 出进程时各自进程内存独立，本锁只约束
    同一进程内（手动触发/进程内回退/v7_autostart）的并发；跨进程由调度错峰约束。
  兼容视图字段（running/period/quick/source/started_at）照旧返回，另加
  periods 明细表（snapshot()），面板无需改动即可读。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_state: Dict[str, Any] = {
    # 兼容视图字段（面板/接口直接读）
    "running": False,
    "period": None,
    "quick": False,
    "source": None,
    "started_at": None,
    "started_mono": None,
    # 结束统计
    "last_finished_at": None,
    "last_period": None,
    "last_report": None,
    "last_error": None,
    "last_elapsed_sec": None,
    "boost_applied": None,
    # [2026-08-27] 分周期运行表：period -> {quick, source, started_at, started_mono}
    "runs": {},
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _refresh_view() -> None:
    """根据 runs 表重建兼容视图字段（调用方必须已持有 _lock）。"""
    runs = _state["runs"]
    if runs:
        newest_p = sorted(runs, key=lambda p: float(runs[p]["started_mono"]))[-1]
        newest = runs[newest_p]
        _state.update({
            "running": True,
            "period": ",".join(sorted(runs)),
            "quick": bool(newest.get("quick")),
            "source": newest.get("source"),
            "started_at": newest.get("started_at"),
            "started_mono": newest.get("started_mono"),
        })
    else:
        _state.update({
            "running": False,
            "period": None,
            "quick": False,
            "source": None,
            "started_at": None,
            "started_mono": None,
        })


def mark_start(*, period: str, quick: bool = False, source: str = "unknown") -> bool:
    """尝试标记开始。同 period 已在跑或超全局并发上限则返回 False。"""
    p = str(period or "")
    with _lock:
        if p in _state["runs"]:
            return False
        try:
            _cap = int(os.getenv("FACTOR_EVO_MAX_CONCURRENT", "2") or 2)
        except (TypeError, ValueError):
            _cap = 2
        _cap = max(1, min(6, _cap))
        if len(_state["runs"]) >= _cap:
            return False
        _state["runs"][p] = {
            "quick": bool(quick),
            "source": source,
            "started_at": _iso_now(),
            "started_mono": time.monotonic(),
        }
        _state["last_error"] = None
        _refresh_view()
    # quick 模式硬超时：到点强制释放该 period 运行态，避免运维台永久「运行中」
    if quick:
        try:
            max_sec = float(os.getenv("FACTOR_EVO_QUICK_MAX_SEC", "300") or 300)
        except (TypeError, ValueError):
            max_sec = 300.0
        max_sec = max(60.0, min(max_sec, 1800.0))

        def _watch(period: str = p) -> None:
            time.sleep(max_sec)
            with _lock:
                active = period in _state["runs"]
            if not active:
                return
            # 仍是同一次 quick
            force_abort(period=period, reason=f"quick_timeout_{int(max_sec)}s")
            logger.warning("[FactorEvo] quick 超时强制结束 max_sec=%s period=%s", max_sec, period)

        threading.Thread(target=_watch, daemon=True, name="evo-quick-watchdog").start()
    return True


def mark_boost(result: Optional[Dict[str, Any]]) -> None:
    with _lock:
        _state["boost_applied"] = result


def mark_end(*, period: Optional[str] = None, report: Optional[Dict[str, Any]] = None,
             error: Optional[str] = None) -> None:
    """结束一个运行。period 缺省时按 started_mono 匹配（兼容旧调用）。"""
    with _lock:
        started_mono = None
        if period is not None:
            run = _state["runs"].pop(str(period), None)
            if run:
                started_mono = run.get("started_mono")
        else:
            legacy_mono = _state.get("started_mono")
            if legacy_mono is not None:
                matched = [
                    _p for _p, _r in _state["runs"].items()
                    if _r.get("started_mono") == legacy_mono
                ]
                if matched:
                    run = _state["runs"].pop(matched[0], None)
                    started_mono = run.get("started_mono") if run else legacy_mono
                else:
                    _state["runs"].clear()
                    started_mono = legacy_mono
            elif _state["runs"]:
                _state["runs"].clear()
        elapsed = None
        if started_mono is not None:
            elapsed = round(time.monotonic() - float(started_mono), 1)
        _state.update({
            "last_finished_at": _iso_now(),
            "last_period": str(period) if period is not None else (_state.get("period") or None),
            "last_report": _summarize_report(report) if report else None,
            "last_error": (error or None) and str(error)[:400],
            "last_elapsed_sec": elapsed,
        })
        _refresh_view()


def snapshot() -> Dict[str, Any]:
    with _lock:
        out = dict(_state)
        runs_view: Dict[str, Any] = {}
        for p, r in _state["runs"].items():
            el = None
            if r.get("started_mono") is not None:
                el = round(time.monotonic() - float(r["started_mono"]), 1)
            runs_view[p] = {
                "quick": bool(r.get("quick")),
                "source": r.get("source"),
                "started_at": r.get("started_at"),
                "elapsed_sec": el,
            }
        out["periods"] = runs_view
        out.pop("runs", None)
    # 运行中实时 elapsed（视图字段口径）
    if out.get("running") and out.get("started_mono") is not None:
        out["elapsed_sec"] = round(time.monotonic() - float(out["started_mono"]), 1)
    else:
        out["elapsed_sec"] = out.get("last_elapsed_sec")
    out.pop("started_mono", None)
    return out


def is_running() -> bool:
    with _lock:
        return bool(_state["runs"])


def force_abort(*, reason: str = "manual", period: Optional[str] = None) -> Dict[str, Any]:
    """强制结束运行态（不杀线程；用于卡住后恢复单飞锁语义）。period 缺省=全部。"""
    with _lock:
        was = bool(_state["runs"])
        snap_before = dict(_state)
        before_periods = sorted(_state["runs"].keys())
        if period is not None:
            targets = [str(period)] if str(period) in _state["runs"] else []
        else:
            targets = list(_state["runs"].keys())
    for _p in targets:
        mark_end(period=_p, error=f"aborted:{reason}"[:400])
    return {"aborted": bool(targets), "before": {
        "period": snap_before.get("period"),
        "source": snap_before.get("source"),
        "quick": snap_before.get("quick"),
        "started_at": snap_before.get("started_at"),
        "periods": before_periods,
    }}


def _summarize_report(report: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(report, dict):
        return {"raw": str(report)[:200]}
    out: Dict[str, Any] = {}
    for k in ("period", "quick", "error", "message", "elapsed_sec", "promoted_factors"):
        if k in report:
            out[k] = report[k]
    # 兼容不同字段名 → 面板统一口径
    mapping = (
        ("n_candidates", ("n_candidates", "candidates", "mined")),
        ("n_evaluated", ("n_evaluated", "evaluated", "eval_count")),
        ("n_survivors", ("n_survivors", "survivors", "purged")),
        ("n_promoted", ("n_promoted", "promoted", "n_promoted_factors")),
    )
    for canon, alts in mapping:
        for alt in alts:
            if alt not in report:
                continue
            v = report[alt]
            out[canon] = len(v) if isinstance(v, (list, dict)) else v
            break
    if "promoted_factors" in report and "n_promoted" not in out:
        pf = report["promoted_factors"]
        out["n_promoted"] = len(pf) if isinstance(pf, list) else pf
    return out


def mining_boost_auto_enabled() -> bool:
    try:
        from backend.services.compute.compute_config import get_value
        return bool(get_value("FACTOR_MINING_BOOST_AUTO"))
    except Exception:  # noqa: BLE001
        import os
        return str(os.getenv("FACTOR_MINING_BOOST_AUTO", "0")).lower() in ("1", "true", "yes", "on")


def ensure_mining_boost_if_auto(*, force: bool = False) -> Optional[Dict[str, Any]]:
    """自动加强档：定时/手动进化前应用 mining_boost（不降门禁）。"""
    if not force and not mining_boost_auto_enabled():
        return None
    try:
        from backend.services.compute.compute_config import apply_preset
        result = apply_preset("mining_boost")
        logger.info("[FactorEvo] mining_boost 已自动应用 ok=%s", result.get("ok"))
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning("[FactorEvo] mining_boost 自动应用失败: %s", e)
        return {"ok": False, "errors": {"__global__": str(e)}}
