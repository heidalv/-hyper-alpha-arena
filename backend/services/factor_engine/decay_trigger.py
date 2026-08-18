"""衰减触发的持续挖掘（升级计划 v3.0 S4/R7）。

活跃因子集 ICIR 均值跌破阈值 → 在低负载窗口自动触发对应周期一轮 full evolution
（与 03:00/04:00/06:00 定时任务去重、每天至多一次、受 1800s 硬预算约束）。
实现「挖掘→门禁→晋升→实盘反馈→衰减→再挖掘」的持续循环。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_STATE_PATH = os.path.join("data", "decay_trigger_state.json")
_DEDUP_SEC = 22 * 3600  # 每周期触发节流（略小于定时任务 24h，避免同日重复）


def _cfg(name: str, default):
    from backend.config import settings as _s
    return getattr(_s, name, default)


def _active_icir_stats() -> Dict[str, Optional[float]]:
    """scalp / midlong 活跃因子的平均 |icir|（store.scores.icir）。"""
    out: Dict[str, Optional[float]] = {"scalp": None, "midlong": None}
    try:
        from backend.services.factor_engine.custom_factor_store import custom_factor_store
        from backend.services.factor_engine.factor_backtest_scorer import _resolve_admin_tenant
        active = custom_factor_store.list_active(tenant_id=_resolve_admin_tenant()) or []
    except Exception:
        return out
    for horizon in ("scalp", "midlong"):
        vals = []
        for r in active:
            _h = str((r.get("extra") or {}).get("horizon") or "scalp").lower()
            if (_h == "midlong") != (horizon == "midlong"):
                continue
            _icir = float((r.get("scores") or {}).get("icir") or 0.0)
            if _icir != 0.0:
                vals.append(abs(_icir))
        out[horizon] = float(sum(vals) / len(vals)) if vals else None
    return out


def maybe_trigger_decay_evolution() -> Dict[str, any]:
    """检查衰减并触发补挖（幂等 + 节流）。返回触发动作。"""
    if not bool(_cfg("FACTOR_EVO_DECAY_TRIGGER", True)):
        return {"triggered": [], "skipped": "disabled"}
    threshold = float(_cfg("FACTOR_EVO_DECAY_TRIGGER_ICIR", 0.02) or 0.02)
    stats = _active_icir_stats()
    state: Dict[str, float] = {}
    try:
        if os.path.exists(_STATE_PATH):
            with open(_STATE_PATH, "r", encoding="utf-8") as f:
                state = json.load(f)
    except Exception:
        state = {}
    triggered = []
    now = time.time()
    for horizon, avg_icir in stats.items():
        if avg_icir is None or avg_icir >= threshold:
            continue
        last = float(state.get(horizon) or 0.0)
        if now - last < _DEDUP_SEC:
            continue
        _period = "4h" if horizon == "midlong" else "5m"
        _horizon_name = "中线(4h)" if horizon == "midlong" else "短线(1h)"
        logger.warning(
            "[DecayTrigger] %s 活跃因子平均|icir|=%.4f < %.3f → 触发补挖（%s）",
            _horizon_name, avg_icir, threshold, _period,
        )
        state[horizon] = now
        triggered.append({"horizon": horizon, "period": _period, "avg_icir": round(avg_icir, 4)})
        try:
            _run_background_evolution(_period, f"decay_trigger:{horizon}")
        except Exception as e:
            logger.warning("[DecayTrigger] 补挖线程启动失败: %s", e)
    if triggered:
        try:
            os.makedirs(os.path.dirname(_STATE_PATH) or ".", exist_ok=True)
            with open(_STATE_PATH, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except Exception as e:
            logger.warning("[DecayTrigger] 状态落盘失败: %s", e)
    return {"triggered": triggered, "stats": stats, "threshold": threshold}


def _run_background_evolution(period: str, source: str) -> None:
    def _run():
        try:
            from backend.services.evolution.factor_evolution_loop import run_factor_evolution_loop
            logger.info("[DecayTrigger] 补挖进化开始 period=%s source=%s", period, source)
            report = run_factor_evolution_loop(period=period, source=source)
            logger.info("[DecayTrigger] 补挖进化完成 period=%s: %s", period, str(report)[:300])
        except Exception as e:
            logger.warning("[DecayTrigger] 补挖进化异常: %s", e)

    t = threading.Thread(target=_run, daemon=True, name=f"decay-evo-{period}")
    t.start()
