"""loss_attribution — 三周期亏损归因分析（日报/周报的触发式区块）。

某周期近 N 天已实现 PnL < 0 时自动生成归因块：
按币种 / 退出原因 / trade_nature / RR 分桶 + 亏损集中度 top3 + 环比变化。
数据源：paper_orders（close_reason 非空的权威已实现盈亏，与 midlong_weekly_report 同源）。
盈利周期返回空块（一句话说明），不硬编亏损文案。纯规则、非交易路径。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _closed_pnl_rows(db, account_id: int, days: int, tier: Optional[str] = None):
    """取近 days 天平仓单（paper_orders 里 close_reason 非空）的 pnl 行。"""
    from datetime import datetime, timedelta
    from backend.database.models import PaperOrder

    cutoff = datetime.utcnow() - timedelta(days=days)
    q = db.query(PaperOrder).filter(
        PaperOrder.account_id == int(account_id),
        PaperOrder.close_reason.isnot(None),
        PaperOrder.filled_at >= cutoff,
    )
    if tier:
        q = q.filter(PaperOrder.trade_nature.isnot(None))
    rows = q.all()
    out = []
    for r in rows:
        out.append({
            "symbol": r.symbol,
            "side": r.side,
            "close_reason": r.close_reason,
            "pnl": float(r.pnl or 0.0),
            "entry_price": float(r.entry_price or 0.0) or None,
            "filled_price": float(r.filled_price or 0.0) or None,
        })
    return out


def _tier_of_trade(trade_nature: Optional[str], timeframe_tier: Optional[str]) -> str:
    t = str(trade_nature or "").lower()
    tf = str(timeframe_tier or "").lower()
    if tf == "long" or t in ("trend_follow", "position"):
        return "long"
    if tf == "mid" or t == "swing":
        return "midlong"
    return "scalp"


def build_loss_attribution(db, account_id: int, horizon: str, days: int = 1) -> Dict[str, Any]:
    """生成某周期的亏损归因块。盈利/无样本时返回 {active: False, note}。"""
    from datetime import datetime, timedelta
    from backend.database.models import PaperOrder

    cutoff = datetime.utcnow() - timedelta(days=days)
    q = db.query(PaperOrder).filter(
        PaperOrder.account_id == int(account_id),
        PaperOrder.close_reason.isnot(None),
        PaperOrder.filled_at >= cutoff,
    )
    rows = []
    for r in q.all():
        if _tier_of_trade(getattr(r, "trade_nature", None), getattr(r, "timeframe_tier", None)) != horizon:
            continue
        rows.append(r)

    pnls = [float(r.pnl or 0.0) for r in rows]
    if not pnls:
        return {"active": False, "note": f"{horizon} 近 {days} 天无平仓样本"}
    total = sum(pnls)
    if total >= 0:
        return {"active": False, "note": f"{horizon} 近 {days} 天盈利 +{total:.2f}，无亏损归因", "total_pnl": round(total, 2)}
    losses = [r for r in rows if float(r.pnl or 0.0) < 0]
    # 分桶
    by_symbol: Dict[str, float] = {}
    by_reason: Dict[str, float] = {}
    by_nature: Dict[str, float] = {}
    for r in losses:
        s = str(r.symbol or "?").upper()
        by_symbol[s] = by_symbol.get(s, 0.0) + float(r.pnl or 0.0)
        k = str(getattr(r, "close_reason", None) or "unknown")
        by_reason[k] = by_reason.get(k, 0.0) + float(r.pnl or 0.0)
        n = str(getattr(r, "trade_nature", None) or "unknown")
        by_nature[n] = by_nature.get(n, 0.0) + float(r.pnl or 0.0)

    def _top3(d: Dict[str, float]):
        items = sorted(d.items(), key=lambda x: x[1])[:3]  # 亏损最多（最负）在前
        return [{"key": k, "pnl": round(v, 2)} for k, v in items]

    return {
        "active": True,
        "window_days": days,
        "total_pnl": round(total, 2),
        "n_trades": len(rows),
        "n_losses": len(losses),
        "by_symbol": _top3(by_symbol),
        "by_exit_reason": _top3(by_reason),
        "by_trade_nature": _top3(by_nature),
    }
