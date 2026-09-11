"""执行延迟测量 — 2026-09-07。

对标 NexusFi：「回测假设信号到执行零延迟；现实中一个延迟的 cancel 能把好
限价单变成坏成交。测量 decision→order-sent 和 order-sent→ack 延迟，记录并
告警异常。」

本模块从 OMS live_orders 读时间戳，计算三段延迟分布：
  - decision→intent：决策到下单意图（用 intent JSONB 里的 decision_ts，
    由 begin_order 注入；无则跳过该段）
  - intent→ack：下单到交易所受理（created_ms → acked 的 updated_ms）
  - intent→filled：下单到成交（created_ms → filled 的 updated_ms）

注意：live_orders 只有 created_ms + updated_ms（最后迁移时间），所以
ack/filled 延迟用「该订单进入终态/ACKED 时的 updated_ms − created_ms」近似。
精确的分段需要迁移历史表（见 order_store 备注）；当前近似已足够发现数量级
异常（秒级 vs 分钟级）。

用法：
  from backend.services.oms.latency_probe import latency_report
  rep = latency_report(hours=24)  # → {n, decision_to_intent_ms: {...}, ...}
"""
from __future__ import annotations

import logging
import os
import statistics
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 延迟告警阈值（毫秒）：超过即视为异常
LATENCY_WARN_ACK_MS = float(os.getenv("LATENCY_WARN_ACK_MS", "10000") or 10000)      # 10s 未受理
LATENCY_WARN_FILL_MS = float(os.getenv("LATENCY_WARN_FILL_MS", "30000") or 30000)    # 30s 未成交


def _pct(sorted_vals: List[float], q: float) -> Optional[float]:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return round(float(sorted_vals[idx]), 1)


def _summary(vals: List[float]) -> Dict[str, Any]:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {
        "n": len(s),
        "p50": _pct(s, 0.5),
        "p90": _pct(s, 0.9),
        "p95": _pct(s, 0.95),
        "max": round(float(s[-1]), 1),
        "mean": round(statistics.fmean(s), 1),
    }


def latency_report(*, hours: float = 24.0, account_id: Optional[int] = None) -> Dict[str, Any]:
    """近 hours 小时的执行延迟分布。fail-open：异常返回 {error}。"""
    try:
        from backend.services.oms.order_store import list_orders, now_ms
        rows = list_orders(
            account_id=account_id,
            since_ms=now_ms() - int(hours * 3600 * 1000),
            limit=2000,
        )
    except Exception as exc:
        return {"error": str(exc)[:200]}

    d2i: List[float] = []   # decision → intent
    i2a: List[float] = []   # intent → ack
    i2f: List[float] = []   # intent → filled
    slow: List[Dict[str, Any]] = []

    for o in rows:
        created = float(o.get("created_ms") or 0)
        updated = float(o.get("updated_ms") or 0)
        status = str(o.get("status") or "").lower()
        if created <= 0:
            continue
        # decision → intent（intent payload 里的 decision_ts）
        intent = o.get("intent") or {}
        if isinstance(intent, str):
            try:
                import json as _j
                intent = _j.loads(intent)
            except Exception:
                intent = {}
        dts = (intent or {}).get("decision_ts")
        if dts:
            try:
                d2i.append(max(0.0, created - float(dts)))
            except (TypeError, ValueError):
                pass
        # intent → ack / filled（updated_ms 是该状态进入时间）
        if status in ("acked", "partial", "filled") and updated > created:
            i2a.append(updated - created)
        if status == "filled" and updated > created:
            i2f.append(updated - created)
            if updated - created > LATENCY_WARN_FILL_MS:
                slow.append({
                    "cid": o.get("client_order_id"), "symbol": o.get("symbol"),
                    "status": status, "fill_ms": round(updated - created, 1),
                })

    return {
        "hours": hours,
        "n_orders": len(rows),
        "decision_to_intent_ms": _summary(d2i),
        "intent_to_ack_ms": _summary(i2a),
        "intent_to_filled_ms": _summary(i2f),
        "slow_fills": slow[:20],
        "warn_thresholds": {"ack_ms": LATENCY_WARN_ACK_MS, "fill_ms": LATENCY_WARN_FILL_MS},
    }
