# -*- coding: utf-8 -*-
"""风控动作剧本（v3 方向 7）：

kill_close_all(dry_run)       急停可选动作：撤全部挂单 + 平全部仓（paper 全部账户；live 账户经
                              LiveExecutor.close_position 逐仓 reduce-only）。默认 dry_run=True 只列清单。
black_swan_playbook(dry_run)  黑天鹅剧本：
   1. TradingState → REDUCING 24h（只平不开），position_scale=0
   2. 新开仓最大杠杆压到 1x（max_leverage=1，TradeGate/调用方读取）
   3. 撤全部 paper 挂单
   4. 趋势桶只留有浮盈仓位：浮亏（unrealized_pnl ≤ 0）的中长线仓位全部平掉；短线仓位全部平掉
   5. carry/套利腿失衡检查（arbitrage_positions 若有 → 报告；再平衡 Phase 2 接入）
   6. 24h 内禁止新策略晋升（promotion_freeze_until）
所有动作都写审计日志并发 P0 告警；dry_run 只返回将要执行的动作清单。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from backend.services.risk.trading_state import TradingState, get_state_store

logger = logging.getLogger(__name__)


def _open_paper_positions(db, account_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    sql = ("SELECT id, account_id, symbol, side, size, entry_price, unrealized_pnl, timeframe_tier, "
           "trade_nature, strategy_id FROM paper_positions WHERE status = 'open'")
    params: Dict[str, Any] = {}
    if account_ids:
        sql += " AND account_id = ANY(:ids)"
        params["ids"] = [int(a) for a in account_ids]
    rows = db.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def _open_paper_orders(db, account_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    from sqlalchemy import text
    sql = "SELECT id, account_id, symbol, side, order_type, status FROM paper_orders WHERE status IN ('pending','open','new')"
    params: Dict[str, Any] = {}
    if account_ids:
        sql += " AND account_id = ANY(:ids)"
        params["ids"] = [int(a) for a in account_ids]
    try:
        rows = db.execute(text(sql), params).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        db.rollback()
        return []


def _live_account_ids(db) -> List[int]:
    """实盘账户：full_auto_sessions.trading_mode='live' 的会话账户（实盘仓位同样落在 paper_positions）。"""
    from sqlalchemy import text
    try:
        rows = db.execute(text(
            "SELECT DISTINCT account_id FROM full_auto_sessions WHERE lower(COALESCE(trading_mode, 'paper')) = 'live'"
        )).fetchall()
        return [int(r[0]) for r in rows if r and r[0] is not None]
    except Exception:
        db.rollback()
        return []


def _cancel_paper_orders(db, orders: List[Dict[str, Any]]) -> int:
    from sqlalchemy import text
    if not orders:
        return 0
    ids = [int(o["id"]) for o in orders]
    n = db.execute(text(
        "UPDATE paper_orders SET status = 'cancelled' WHERE id = ANY(:ids) AND status IN ('pending','open','new')"
    ), {"ids": ids}).rowcount or 0
    db.commit()
    return int(n)


def _close_paper_position(db, pos: Dict[str, Any], reason: str) -> Dict[str, Any]:
    from backend.services.paper_trading_engine import paper_engine
    try:
        res = paper_engine.close_position(
            db, int(pos["account_id"]), str(pos["symbol"]), str(pos["side"]),
            reason=reason, strategy_id=pos.get("strategy_id"), position_id=int(pos["id"]),
            trade_nature=pos.get("trade_nature"),
        )
        ok = bool(res) and not (isinstance(res, dict) and res.get("success") is False)
        summary = None
        if isinstance(res, dict):
            summary = {k: res.get(k) for k in ("success", "pnl", "fill_price", "reason") if k in res}
        return {"position_id": pos["id"], "symbol": pos["symbol"], "side": pos["side"], "ok": ok, "result": summary}
    except Exception as exc:
        logger.error("[Playbook] paper 平仓失败 pos=%s: %s", pos.get("id"), exc)
        try:
            db.rollback()
        except Exception:
            pass
        return {"position_id": pos["id"], "symbol": pos["symbol"], "side": pos["side"], "ok": False, "error": str(exc)[:200]}


def _close_live_position(db, pos: Dict[str, Any], reason: str) -> Dict[str, Any]:
    try:
        from backend.services.exchange.live_executor import LiveExecutor
        ex = LiveExecutor()
        res = ex.close_position(db, int(pos["account_id"]), str(pos["symbol"]), str(pos["side"]),
                                reason=reason, trade_nature=pos.get("trade_nature"))
        ok = bool(getattr(res, "success", False))
        return {"position_id": pos["id"], "symbol": pos["symbol"], "side": pos["side"], "ok": ok,
                "live": True, "message": str(getattr(res, "message", ""))[:200]}
    except Exception as exc:
        logger.error("[Playbook] live 平仓失败 pos=%s: %s", pos.get("id"), exc)
        return {"position_id": pos["id"], "symbol": pos["symbol"], "side": pos["side"], "ok": False,
                "live": True, "error": str(exc)[:200]}


def kill_close_all(db, *, dry_run: bool = True, include_live: bool = True, reason: str = "kill_switch") -> Dict[str, Any]:
    """撤全部挂单 + 平全部仓。dry_run=True 只列清单。"""
    live_ids = set(_live_account_ids(db)) if include_live else set()
    positions = _open_paper_positions(db)
    orders = _open_paper_orders(db)
    plan = {
        "dry_run": dry_run, "reason": reason, "ts": time.time(),
        "orders_to_cancel": len(orders),
        "positions": [{"id": p["id"], "account_id": p["account_id"], "symbol": p["symbol"], "side": p["side"],
                       "size": p["size"], "upnl": p["unrealized_pnl"], "tier": p["timeframe_tier"],
                       "live": int(p["account_id"]) in live_ids} for p in positions],
        "executed": [], "cancelled": 0,
    }
    if dry_run:
        return plan
    plan["cancelled"] = _cancel_paper_orders(db, orders)
    for p in positions:
        if int(p["account_id"]) in live_ids:
            plan["executed"].append(_close_live_position(db, p, reason))
        else:
            plan["executed"].append(_close_paper_position(db, p, reason))
    ok_n = sum(1 for e in plan["executed"] if e.get("ok"))
    _alert(f"🛑 急停平仓执行：撤单 {plan['cancelled']}，平仓 {ok_n}/{len(plan['executed'])} 成功\n原因: {reason}")
    logger.critical("[Playbook] kill_close_all 完成: cancelled=%s closed=%s/%s", plan["cancelled"], ok_n, len(plan["executed"]))
    return plan


def black_swan_playbook(db, *, dry_run: bool = True, reason: str = "black_swan", hours: float = 24.0) -> Dict[str, Any]:
    store = get_state_store()
    positions = _open_paper_positions(db)
    orders = _open_paper_orders(db)
    live_ids = set(_live_account_ids(db))

    def _is_scalp(p):
        return str(p.get("trade_nature") or "").lower() == "scalp" or str(p.get("timeframe_tier") or "").lower() == "short"

    to_close = [p for p in positions if _is_scalp(p) or float(p.get("unrealized_pnl") or 0) <= 0]
    to_keep = [p for p in positions if p not in to_close]
    arb_legs: List[Dict[str, Any]] = []
    arb_imbalance: List[Dict[str, Any]] = []
    try:
        from sqlalchemy import text
        arb_legs = [dict(r) for r in db.execute(text(
            "SELECT id, position_id, symbol, status, long_size, short_size, delta, size_usd "
            "FROM arbitrage_positions WHERE status IN ('open','active') LIMIT 50"
        )).mappings().all()]
        for leg in arb_legs:
            long_s = abs(float(leg.get("long_size") or 0))
            short_s = abs(float(leg.get("short_size") or 0))
            size = long_s + short_s
            delta = abs(float(leg.get("delta") or (long_s - short_s)))
            ratio = (delta / size) if size > 0 else 0.0
            if ratio > 0.05:  # >5% 名义失衡
                arb_imbalance.append({
                    "position_id": leg.get("position_id") or leg.get("id"),
                    "symbol": leg.get("symbol"),
                    "delta": delta, "size": size, "imbalance_ratio": round(ratio, 4),
                    "action": "rebalance_required",
                })
    except Exception:
        db.rollback()

    plan: Dict[str, Any] = {
        "dry_run": dry_run, "reason": reason, "ts": time.time(),
        "actions": [
            f"TradingState → REDUCING {hours:.0f}h, position_scale=0",
            "max_leverage → 1x",
            f"撤 paper 挂单 {len(orders)} 张",
            f"平掉浮亏/短线仓位 {len(to_close)} 个；保留有浮盈中长线仓位 {len(to_keep)} 个",
            f"套利腿检查：{len(arb_legs)} 条；失衡需再平衡 {len(arb_imbalance)} 条",
            f"promotion_freeze {hours:.0f}h",
        ],
        "close_list": [{"id": p["id"], "symbol": p["symbol"], "side": p["side"], "upnl": p["unrealized_pnl"],
                        "tier": p["timeframe_tier"], "live": int(p["account_id"]) in live_ids} for p in to_close],
        "keep_list": [{"id": p["id"], "symbol": p["symbol"], "side": p["side"], "upnl": p["unrealized_pnl"]} for p in to_keep],
        "arb_imbalance": arb_imbalance,
        "executed": [], "cancelled": 0, "carry_rebalance": [],
    }
    if dry_run:
        return plan

    store.set_state(TradingState.REDUCING, reason=reason, source="black_swan", ttl_seconds=hours * 3600.0,
                    position_scale=0.0, max_leverage=1.0)
    store.set_promotion_freeze(hours * 3600.0, reason)
    plan["cancelled"] = _cancel_paper_orders(db, orders)
    for p in to_close:
        if int(p["account_id"]) in live_ids:
            plan["executed"].append(_close_live_position(db, p, reason))
        else:
            plan["executed"].append(_close_paper_position(db, p, reason))
    # carry 再平衡：本阶段记录意图；真补腿需双所客户端（避免黑天鹅时乱开仓）
    for imb in arb_imbalance:
        plan["carry_rebalance"].append({
            **imb,
            "status": "reported_only",
            "note": "黑天鹅期间只报告失衡，不自动开对冲腿（防踩踏）",
        })
    ok_n = sum(1 for e in plan["executed"] if e.get("ok"))
    _alert(f"🦢 黑天鹅剧本执行：REDUCING {hours:.0f}h、杠杆 1x、撤单 {plan['cancelled']}、平仓 {ok_n}/{len(to_close)}、保留 {len(to_keep)}、套利失衡 {len(arb_imbalance)}\n原因: {reason}")
    logger.critical("[Playbook] black_swan 完成: %s", {k: v for k, v in plan.items() if k in ("cancelled", "reason")})
    return plan


def _alert(text: str) -> None:
    try:
        from backend.services.ops.alerts import send_alert
        send_alert("P0", "风控剧本", text, dedupe_key=None, source="risk_playbook")
    except Exception as exc:  # pragma: no cover
        logger.debug("[Playbook] 告警失败: %s", exc)
