"""专职退出 Agent（2026-08-17 新增 Agent，审计缺口 #2）。

职责：跨 tier 的退出协调与风控诊断——不重复现有的分段止盈/追踪止损
（PEO + v2 protection 已覆盖），只做它们不做的三件事：
1. **时间止损**：持仓超过 tier 最大持有时间 → 建议退出；
2. **同向叠加预警**：同一方向持仓数/保证金超过风险预算 → 建议减仓；
3. **退出健康统计**：各 tier 的退出原因分布（供因果回灌与复盘）。

安全设计：默认仅建议（EXIT_AGENT_EXECUTE=false），建议经日志与 stats
可见；开启执行后，时间止损经现有 close 路径平仓（仍带风控二次校验）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# tier 最大持有时间（秒）：短线 2h / 中线 48h / 长线 240h
_DEFAULT_MAX_HOLD_SEC = {"short": 2 * 3600, "mid": 48 * 3600, "long": 240 * 3600}

_stats: Dict[str, Any] = {
    "passes": 0, "time_stop_advised": 0, "stack_warn": 0, "executed": 0, "last_run_ts": 0,
}
_lock = threading.Lock()


def _execute_enabled() -> bool:
    return os.getenv("EXIT_AGENT_EXECUTE", "false").strip().lower() in ("1", "true", "yes", "on")


def _max_hold_sec(tier: str) -> int:
    raw = os.getenv("EXIT_AGENT_MAX_HOLD_SEC_" + str(tier).upper(), "")
    try:
        return int(raw) if raw else _DEFAULT_MAX_HOLD_SEC.get(str(tier).lower(), 48 * 3600)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_HOLD_SEC.get(str(tier).lower(), 48 * 3600)


def run_exit_pass(db, positions: List[Dict[str, Any]], market_summary: Dict[str, Any]) -> Dict[str, Any]:
    """对全部开仓做一次退出建议扫描。返回 {advice: [...], stats}。"""
    advice: List[Dict[str, Any]] = []
    now = time.time()
    dir_stack: Dict[str, Dict[str, Any]] = {}  # side → {count, margin}

    for p in positions or []:
        if str(p.get("status") or "open") != "open":
            continue
        pid = p.get("id")
        sym = str(p.get("symbol") or "").upper()
        side = str(p.get("side") or "").lower()
        tier = str(p.get("tier") or p.get("timeframe_tier") or "mid").lower()
        entry = float(p.get("entry_price") or 0) or 0.0
        mark = float(p.get("mark_price") or p.get("current_price") or entry) or entry
        margin = float(p.get("margin") or 0) or 0.0
        opened_at = p.get("opened_at")

        # 同向叠加统计
        st = dir_stack.setdefault(side if side else "unknown", {"count": 0, "margin": 0.0})
        st["count"] += 1
        st["margin"] += margin

        # 1) 时间止损
        hold_sec = None
        if opened_at is not None:
            try:
                from datetime import datetime
                if isinstance(opened_at, str):
                    opened_at = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                hold_sec = now - opened_at.timestamp()
            except Exception:  # noqa: BLE001
                hold_sec = None
        if hold_sec is not None and hold_sec > _max_hold_sec(tier):
            pnl_pct = ((mark - entry) / entry * 100) if entry > 0 else 0.0
            if side == "short":
                pnl_pct = -pnl_pct
            advice.append({
                "position_id": pid, "symbol": sym, "tier": tier, "side": side,
                "kind": "time_stop", "hold_hours": round(hold_sec / 3600, 1),
                "pnl_pct": round(pnl_pct, 3),
                "reason": f"持仓 {hold_sec / 3600:.1f}h 超过 tier 上限 {_max_hold_sec(tier) / 3600:.1f}h",
            })

    # 2) 同向叠加预警（单方向 ≥ 6 个持仓）
    for side, st in dir_stack.items():
        if st["count"] >= 6:
            advice.append({
                "position_id": None, "symbol": "*", "tier": "*", "side": side,
                "kind": "stack_warn", "count": st["count"], "margin": round(st["margin"], 2),
                "reason": f"{side} 方向叠加 {st['count']} 个持仓，建议检查风险预算",
            })

    with _lock:
        _stats["passes"] += 1
        _stats["time_stop_advised"] += sum(1 for a in advice if a["kind"] == "time_stop")
        _stats["stack_warn"] += sum(1 for a in advice if a["kind"] == "stack_warn")
        _stats["last_run_ts"] = now

    # 3) 执行（默认关）：时间止损走现有 close 路径
    executed = 0
    if _execute_enabled():
        from backend.services.paper_trading_engine import paper_engine
        for a in advice:
            if a["kind"] != "time_stop" or not a.get("position_id"):
                continue
            try:
                res = paper_engine.close_position(
                    db=db, position_id=int(a["position_id"]), reason="exit_agent_time_stop",
                )
                if res:
                    executed += 1
                    logger.warning(
                        "[ExitAgent] 时间止损执行 %s #%s (%.1fh) res=%s",
                        a["symbol"], a["position_id"], a["hold_hours"], res,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[ExitAgent] 时间止损执行失败 #%s: %s", a["position_id"], exc)
        with _lock:
            _stats["executed"] += executed

    for a in advice:
        logger.info("[ExitAgent] %s %s/%s %s: %s", a["kind"], a["symbol"], a["side"], a["tier"], a["reason"])
    return {"advice": advice, "stats": status(), "executed": executed}


def status() -> Dict[str, Any]:
    with _lock:
        return dict(_stats)
