"""因子进化运行时状态（供 Ops / compute status 轮询）。

定时 cron 与手动触发共用同一套状态，避免「后台在跑但面板显示空闲」。
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_lock = threading.RLock()
_state: Dict[str, Any] = {
    "running": False,
    "period": None,
    "quick": False,
    "source": None,  # cron | manual | quick | unknown
    "started_at": None,
    "started_mono": None,
    "last_finished_at": None,
    "last_period": None,
    "last_report": None,
    "last_error": None,
    "last_elapsed_sec": None,
    "boost_applied": None,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def mark_start(*, period: str, quick: bool = False, source: str = "unknown") -> bool:
    """尝试标记开始。已在跑则返回 False（单飞）。"""
    with _lock:
        if _state["running"]:
            return False
        _state.update({
            "running": True,
            "period": period,
            "quick": bool(quick),
            "source": source,
            "started_at": _iso_now(),
            "started_mono": time.monotonic(),
            "last_error": None,
            "boost_applied": None,
        })
    # quick 模式硬超时：到点强制释放运行态，避免运维台永久「运行中」
    if quick:
        try:
            max_sec = float(__import__("os").getenv("FACTOR_EVO_QUICK_MAX_SEC", "300") or 300)
        except (TypeError, ValueError):
            max_sec = 300.0
        max_sec = max(60.0, min(max_sec, 1800.0))

        def _watch() -> None:
            time.sleep(max_sec)
            with _lock:
                if not _state["running"] or not _state.get("quick"):
                    return
                started = _state.get("started_mono")
            if started is None:
                return
            # 仍是同一次 quick
            force_abort(reason=f"quick_timeout_{int(max_sec)}s")
            logger.warning("[FactorEvo] quick 超时强制结束 max_sec=%s", max_sec)

        threading.Thread(target=_watch, daemon=True, name="evo-quick-watchdog").start()
    return True


def mark_boost(result: Optional[Dict[str, Any]]) -> None:
    with _lock:
        _state["boost_applied"] = result


def mark_end(*, report: Optional[Dict[str, Any]] = None, error: Optional[str] = None) -> None:
    with _lock:
        elapsed = None
        if _state.get("started_mono") is not None:
            elapsed = round(time.monotonic() - float(_state["started_mono"]), 1)
        _state.update({
            "running": False,
            "last_finished_at": _iso_now(),
            "last_period": _state.get("period"),
            "last_report": _summarize_report(report) if report else None,
            "last_error": (error or None) and str(error)[:400],
            "last_elapsed_sec": elapsed,
            "period": None,
            "quick": False,
            "source": None,
            "started_at": None,
            "started_mono": None,
        })


def snapshot() -> Dict[str, Any]:
    with _lock:
        out = dict(_state)
    # 运行中实时 elapsed
    if out.get("running") and out.get("started_mono") is not None:
        out["elapsed_sec"] = round(time.monotonic() - float(out["started_mono"]), 1)
    else:
        out["elapsed_sec"] = out.get("last_elapsed_sec")
    out.pop("started_mono", None)
    return out


def is_running() -> bool:
    with _lock:
        return bool(_state["running"])


def force_abort(*, reason: str = "manual") -> Dict[str, Any]:
    """强制结束运行态（不杀线程；用于卡住后恢复单飞锁语义）。"""
    with _lock:
        was = bool(_state["running"])
        snap_before = dict(_state)
    if was:
        mark_end(error=f"aborted:{reason}"[:400])
    return {"aborted": was, "before": {
        "period": snap_before.get("period"),
        "source": snap_before.get("source"),
        "quick": snap_before.get("quick"),
        "started_at": snap_before.get("started_at"),
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
