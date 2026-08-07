"""
Paper 账户权益曲线

仪表盘「当前」数字来自 paper_balances.total_equity，
但旧的 account_asset_snapshots /asset-curve/timeframe 走的是 Arena/AI 账户模型，
paper 几乎不写快照，导致图表停在初始资金或空白。

本模块：
1. 优先用已有 AccountAssetSnapshot（若足够）
2. 否则用 paper_orders 累计重建「已实现权益路径」
3. 末点始终对齐当前 paper 总权益（含浮动）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_PERIOD_DAYS = {"7d": 7, "30d": 30, "all": None}


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _ts(dt: datetime) -> int:
    return int(_ensure_utc(dt).timestamp())


def build_paper_equity_curve(
    db: Session,
    account_id: int,
    period: str = "7d",
    *,
    max_points: int = 400,
) -> Dict[str, Any]:
    from backend.database.models import (
        Account,
        AccountAssetSnapshot,
        PaperBalance,
        PaperOrder,
    )

    period_key = (period or "7d").lower().strip()
    if period_key not in _PERIOD_DAYS:
        period_key = "7d"

    account = db.query(Account).filter(Account.id == int(account_id)).first()
    if not account:
        return {"account_id": account_id, "period": period_key, "points": [], "source": "none"}

    bal = db.query(PaperBalance).filter(PaperBalance.account_id == int(account_id)).first()
    initial = float(bal.initial_balance) if bal else float(account.initial_capital or 0)
    now = datetime.now(timezone.utc)

    # 当前权益（含浮动）
    current_equity = initial
    try:
        from backend.services.paper_trading_engine import paper_engine

        live = paper_engine.get_balance(db, int(account_id)) or {}
        if live.get("total_equity") is not None:
            current_equity = float(live["total_equity"])
        elif bal:
            current_equity = float(bal.total_equity or initial)
    except Exception as e:
        logger.debug("[PaperEquityCurve] live balance: %s", e)
        if bal:
            current_equity = float(bal.total_equity or initial)

    cutoff: Optional[datetime] = None
    days = _PERIOD_DAYS[period_key]
    if days is not None:
        cutoff = now - timedelta(days=days)

    points: List[Dict[str, Any]] = []
    source = "orders"

    # 1) 快照路径（若 paper 已开始写入）
    snap_q = db.query(AccountAssetSnapshot).filter(
        AccountAssetSnapshot.account_id == int(account_id)
    )
    if cutoff is not None:
        snap_q = snap_q.filter(AccountAssetSnapshot.event_time >= cutoff.replace(tzinfo=None))
    snaps = snap_q.order_by(AccountAssetSnapshot.event_time.asc()).all()
    if len(snaps) >= 2:
        source = "snapshots"
        for s in snaps:
            et = s.event_time
            if et is None:
                continue
            points.append(
                {
                    "time": _ts(et),
                    "value": round(float(s.total_assets or 0), 4),
                }
            )
    else:
        # 2) 订单重建：equity ≈ initial + Σpnl − Σfee（末点再叠当前浮动）
        source = "orders"
        oq = (
            db.query(PaperOrder)
            .filter(
                PaperOrder.account_id == int(account_id),
                PaperOrder.status == "filled",
                PaperOrder.filled_at.isnot(None),
            )
            .order_by(PaperOrder.filled_at.asc())
        )
        orders = oq.all()

        start_dt = None
        if bal and bal.last_reset_at:
            start_dt = bal.last_reset_at
        elif bal and bal.created_at:
            start_dt = bal.created_at
        elif orders:
            start_dt = orders[0].filled_at
        else:
            start_dt = now

        start_dt = _ensure_utc(start_dt)
        if cutoff is not None and start_dt < cutoff:
            # 时段起点：先滚到 cutoff 前的累计权益
            equity = initial
            for o in orders:
                ft = o.filled_at
                if ft is None:
                    continue
                ft_u = _ensure_utc(ft)
                if ft_u >= cutoff:
                    break
                equity += float(o.pnl or 0) - float(o.fee or 0)
            points.append({"time": _ts(cutoff), "value": round(equity, 4)})
            running = equity
            for o in orders:
                ft = o.filled_at
                if ft is None:
                    continue
                ft_u = _ensure_utc(ft)
                if ft_u < cutoff:
                    continue
                running += float(o.pnl or 0) - float(o.fee or 0)
                points.append({"time": _ts(ft_u), "value": round(running, 4)})
        else:
            points.append({"time": _ts(start_dt), "value": round(initial, 4)})
            running = initial
            for o in orders:
                ft = o.filled_at
                if ft is None:
                    continue
                ft_u = _ensure_utc(ft)
                if cutoff is not None and ft_u < cutoff:
                    continue
                running += float(o.pnl or 0) - float(o.fee or 0)
                points.append({"time": _ts(ft_u), "value": round(running, 4)})

    # 末点对齐当前权益
    now_ts = _ts(now)
    if points and points[-1]["time"] == now_ts:
        points[-1]["value"] = round(current_equity, 4)
    else:
        points.append({"time": now_ts, "value": round(current_equity, 4)})

    # 去重同秒（保留最后一个）
    dedup: Dict[int, float] = {}
    for p in points:
        dedup[int(p["time"])] = float(p["value"])
    cleaned = [{"time": t, "value": v} for t, v in sorted(dedup.items())]

    # 降采样
    if len(cleaned) > max_points:
        step = max(1, len(cleaned) // max_points)
        sampled = cleaned[::step]
        if sampled[-1]["time"] != cleaned[-1]["time"]:
            sampled.append(cleaned[-1])
        cleaned = sampled

    # 至少 2 点才能画线：若只有末点，补一个起点
    if len(cleaned) == 1:
        t1 = cleaned[0]["time"]
        cleaned = [
            {"time": max(0, t1 - 3600), "value": round(initial, 4)},
            cleaned[0],
        ]

    # 强制末点 = 当前权益（与仪表盘「当前」一致，含浮动）
    now_ts2 = _ts(datetime.now(timezone.utc))
    if cleaned[-1]["time"] >= now_ts2 - 2:
        cleaned[-1] = {"time": cleaned[-1]["time"], "value": round(current_equity, 4)}
    else:
        cleaned.append({"time": now_ts2, "value": round(current_equity, 4)})

    return {
        "account_id": int(account_id),
        "account_name": account.name,
        "period": period_key,
        "source": source,
        "initial_balance": round(initial, 4),
        "current_equity": round(current_equity, 4),
        "points": cleaned,
    }


def record_paper_equity_snapshot(db: Session, account_id: int) -> None:
    """把当前 paper 权益写入 account_asset_snapshots（供后续走快照路径）。"""
    from backend.database.models import AccountAssetSnapshot
    from backend.services.asset_curve_calculator import invalidate_asset_curve_cache
    from backend.services.paper_trading_engine import paper_engine

    live = paper_engine.get_balance(db, int(account_id)) or {}
    equity = float(live.get("total_equity") or 0)
    cash = float(live.get("available_balance") or 0)
    frozen = float(live.get("frozen_margin") or 0)
    upnl = float(live.get("unrealized_pnl") or 0)
    if equity <= 0:
        return
    db.add(
        AccountAssetSnapshot(
            account_id=int(account_id),
            total_assets=equity,
            cash=cash,
            positions_value=frozen + upnl,
            trigger_symbol="paper",
            trigger_market="PAPER",
            event_time=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )
    try:
        db.commit()
        invalidate_asset_curve_cache()
    except Exception:
        db.rollback()
        raise
