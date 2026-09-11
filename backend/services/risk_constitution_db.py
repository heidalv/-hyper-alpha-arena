"""宪法风控的 DB 查询（与常量分离，便于单测 mock）。

只读查询，全部 fail-open 返回 None（拿不到数据不阻断，由调用方决定）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict

logger = logging.getLogger(__name__)


def daily_loss_pct(account_id: int) -> Optional[float]:
    """当日已实现净亏损 / 净值（正数=亏损比例）。无数据/无亏损返回 0.0；出错 None。"""
    db = None
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        row = db.execute(
            text("""
                SELECT COALESCE(SUM(
                    (close_price - entry_price) * CASE WHEN lower(side)='long' THEN 1 ELSE -1 END * size
                    + COALESCE(partial_realized_pnl, 0)
                ), 0) AS day_pnl
                FROM paper_positions
                WHERE account_id = :acct AND status = 'closed'
                  AND closed_at >= :day_start
            """),
            {"acct": int(account_id),
             "day_start": datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)},
        ).mappings().first()
        eq_row = db.execute(
            text("SELECT total_equity FROM paper_balances WHERE account_id = :acct"),
            {"acct": int(account_id)},
        ).mappings().first()
        equity = float((eq_row or {}).get("total_equity") or 0)
        pnl = float((row or {}).get("day_pnl") or 0)
        if equity <= 0:
            return None
        return abs(pnl) / equity if pnl < 0 else 0.0
    except Exception as exc:
        logger.debug("[Constitution] daily_loss_pct 失败: %s", exc)
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass


def margin_usage(account_id: int, *, symbol: Optional[str] = None) -> Optional[Dict[str, float]]:
    """当前未平仓保证金占用：{symbol_margin_pct, total_margin_pct}（相对净值）。"""
    db = None
    try:
        from sqlalchemy import text
        from backend.database.connection import SessionLocal
        db = SessionLocal()
        rows = db.execute(
            text("""
                SELECT symbol, COALESCE(SUM(margin), 0) AS m
                FROM paper_positions
                WHERE account_id = :acct AND status = 'open'
                GROUP BY symbol
            """),
            {"acct": int(account_id)},
        ).mappings().all()
        eq_row = db.execute(
            text("SELECT total_equity FROM paper_balances WHERE account_id = :acct"),
            {"acct": int(account_id)},
        ).mappings().first()
        equity = float((eq_row or {}).get("total_equity") or 0)
        if equity <= 0:
            return None
        total = sum(float(r["m"] or 0) for r in rows)
        sym_m = 0.0
        if symbol:
            for r in rows:
                if str(r["symbol"] or "").upper() == str(symbol).upper():
                    sym_m = float(r["m"] or 0)
                    break
        return {
            "symbol_margin_pct": sym_m / equity,
            "total_margin_pct": total / equity,
        }
    except Exception as exc:
        logger.debug("[Constitution] margin_usage 失败: %s", exc)
        return None
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
