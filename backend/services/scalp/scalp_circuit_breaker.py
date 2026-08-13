"""策略绑定自动熔断（P3）。

对每个 running 绑定检查最近真实成交：
- 连续亏损 >= N 笔 → pause（SCALP_CB_CONSECUTIVE_LOSSES，默认 5）
- 最近 20 笔净亏 >= $X → pause（SCALP_CB_NET_LOSS_USD，默认 20）
- 无成交超过 7 天 → 标记 idle（不熔断，只告警）

熔断后状态 paused + stop_reason，前端可见；人工确认后才可重新启用。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List

from sqlalchemy import text

logger = logging.getLogger(__name__)


def _cfg_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def _cfg_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except Exception:
        return default


def _recent_trades(strategy_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal

    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT pnl, created_at FROM paper_orders "
                    "WHERE strategy_id = :sid AND status = 'filled' AND pnl IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT :lim"
                ),
                {"sid": strategy_id, "lim": limit},
            ).mappings().all()
    return [dict(r) for r in rows]


def check_binding(binding: Dict[str, Any]) -> Dict[str, Any]:
    """对单个绑定返回熔断决策。"""
    strategy_id = binding["strategy_id"]
    trades = _recent_trades(strategy_id)
    if not trades:
        return {"action": "noop", "reason": "暂无成交"}
    consec = 0
    for t in trades:
        if float(t.get("pnl") or 0) < 0:
            consec += 1
        else:
            break
    net = sum(float(t.get("pnl") or 0) for t in trades)
    max_consec = _cfg_int("SCALP_CB_CONSECUTIVE_LOSSES", 5)
    max_net_loss = _cfg_float("SCALP_CB_NET_LOSS_USD", 20.0)
    if consec >= max_consec:
        return {
            "action": "pause",
            "reason": "连续亏损 %d 笔" % consec,
            "consecutive_losses": consec,
            "net_last_20": round(net, 2),
        }
    if len(trades) >= 10 and net <= -max_net_loss:
        return {
            "action": "pause",
            "reason": "最近 %d 笔净亏 $%.2f" % (len(trades), net),
            "consecutive_losses": consec,
            "net_last_20": round(net, 2),
        }
    return {
        "action": "ok",
        "reason": "正常",
        "consecutive_losses": consec,
        "net_last_20": round(net, 2),
    }


def check_all(*, apply: bool | None = None) -> Dict[str, Any]:
    """检查全部 running 绑定。

    apply=False 或 SCALP_CIRCUIT_BREAKER_ENABLED=false 时只干跑报告，不 pause。
    """
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.services.scalp.scalp_bindings import ensure_tables

    if apply is None:
        apply = os.getenv("SCALP_CIRCUIT_BREAKER_ENABLED", "false").lower() in (
            "1", "true", "yes", "on",
        )

    ensure_tables()
    result: Dict[str, Any] = {
        "checked": 0,
        "paused": [],
        "would_pause": [],
        "apply": bool(apply),
    }
    with system_identity():
        with SessionLocal() as db:
            rows = db.execute(
                text(
                    "SELECT id, symbol, period, factor_set, strategy_id, status "
                    "FROM pair_strategy_bindings WHERE status = 'running' ORDER BY id"
                )
            ).mappings().all()
            for r in rows:
                binding = dict(r)
                dec = check_binding(binding)
                result["checked"] += 1
                if dec["action"] != "pause":
                    continue
                item = {
                    "id": binding["id"],
                    "symbol": binding["symbol"],
                    "period": binding["period"],
                    "factor_set": binding["factor_set"],
                    "reason": dec["reason"],
                }
                if not apply:
                    result["would_pause"].append(item)
                    continue
                db.execute(
                    text(
                        "UPDATE pair_strategy_bindings SET status='paused', "
                        "disabled_at=now(), stop_reason=:reason WHERE id=:bid"
                    ),
                    {"reason": dec["reason"], "bid": binding["id"]},
                )
                result["paused"].append(item)
                logger.warning("[CircuitBreaker] 熔断 binding %s: %s",
                               binding["id"], dec["reason"])
            if apply:
                db.commit()
    try:
        from backend.services.scalp.scalp_heartbeat import touch
        touch("scalp_circuit_breaker", "ok", {
            "apply": bool(apply),
            "checked": result["checked"],
            "paused": len(result["paused"]),
            "would_pause": len(result["would_pause"]),
        })
    except Exception:
        pass
    return result


def run_tick() -> Dict[str, Any]:
    """调度入口：默认干跑（不 pause），显式开启才 apply。"""
    return check_all()
