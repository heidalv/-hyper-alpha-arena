# -*- coding: utf-8 -*-
"""trade_facts 日对账回填（v3 F2c）。

背景
----
trade_facts 是学习层（校准 / IC / 复盘 / 因子归因）的唯一样本仓库，但它靠
close_position 里一次"尽力而为"的独立事务写入，失败只打 debug 日志。实测
近 7 天有 10 笔 mid 仓（midlong:no_progress / trend_broken）没有样本 —— 根因
是 close_reason 列 VARCHAR(64) 被 120 字符的中长线出场原因撑爆，INSERT 静默失败。
学习层因此系统性地缺失"中长线治理出场"这一类样本，偏差方向不可知。

本模块做两件事：
1. schema 自愈：close_reason 放宽到 VARCHAR(200)，补 backfilled 标记列；
2. 对账回填：窗口内 status ∈ {closed, liquidated} 但没有 trade_facts 的持仓，
   按 close_position 同一口径补一行（fees=平仓费估算+部分平仓费，pnl=毛盈亏，
   outcome 按净额），并标 backfilled=TRUE 以便区分真值与回填。

由定时任务每日调用，也可通过 /api/ops/edge-ledger/reconcile 手动触发。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# 与 sub_position_manager.NATURE_TO_TIER 保持一致的兜底映射
_NATURE_TO_TIER = {
    "scalp": "short", "intraday": "short",
    "swing": "mid",
    "trend_follow": "long", "position": "long",
}

_SCHEMA_READY = False


def ensure_trade_facts_schema(db) -> None:
    """幂等：建表（与 paper_engine._write_trade_fact 同 DDL）+ 放宽 close_reason + 补 backfilled 列。"""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    from sqlalchemy import text as _t
    db.execute(_t(
        "CREATE TABLE IF NOT EXISTS trade_facts ("
        " id BIGSERIAL PRIMARY KEY,"
        " ts TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " source VARCHAR(8) NOT NULL DEFAULT 'paper',"
        " account_id INT NOT NULL,"
        " position_id VARCHAR(64) NOT NULL,"
        " symbol VARCHAR(32) NOT NULL,"
        " tier VARCHAR(8) NOT NULL,"
        " side VARCHAR(8) NOT NULL,"
        " entry_price DOUBLE PRECISION,"
        " exit_price DOUBLE PRECISION,"
        " fees DOUBLE PRECISION,"
        " pnl DOUBLE PRECISION,"
        " outcome VARCHAR(16),"
        " close_reason VARCHAR(200),"
        " factor_exposures JSONB,"
        " resonance JSONB)"
    ))
    for ddl in (
        "ALTER TABLE trade_facts ALTER COLUMN close_reason TYPE VARCHAR(200)",
        "ALTER TABLE trade_facts ADD COLUMN IF NOT EXISTS strategy_id VARCHAR(64) NOT NULL DEFAULT ''",
        "ALTER TABLE trade_facts ADD COLUMN IF NOT EXISTS backfilled BOOLEAN NOT NULL DEFAULT FALSE",
        "CREATE INDEX IF NOT EXISTS ix_trade_facts_acct_pos ON trade_facts (account_id, position_id)",
    ):
        try:
            db.execute(_t(ddl))
        except Exception as exc:  # 并发/权限等，不阻断
            logger.debug("[TradeFactsReconcile] DDL 跳过 (%s): %s", ddl[:60], exc)
    db.commit()
    _SCHEMA_READY = True


def _taker_rate() -> float:
    try:
        from backend.services.backtest_engine.backtest_engine import TAKER_FEE
        return float(TAKER_FEE)
    except Exception:
        return 0.00035


def backfill_missing_trade_facts(db, *, days: int = 3, account_id: Optional[int] = None,
                                 limit: int = 2000) -> Dict[str, Any]:
    """回填窗口内缺失的 trade_facts；返回 {"checked", "inserted", "errors"}。"""
    from sqlalchemy import text as _t
    ensure_trade_facts_schema(db)
    since = datetime.now() - timedelta(days=int(days))
    rate = _taker_rate()
    params: Dict[str, Any] = {"since": since, "lim": int(limit)}
    acct_clause = ""
    if account_id is not None:
        acct_clause = " AND p.account_id = :acct"
        params["acct"] = int(account_id)
    rows = db.execute(_t(
        f"""
        SELECT p.id, p.account_id, p.symbol, p.side, COALESCE(p.timeframe_tier, '') AS tier,
               COALESCE(p.trade_nature, '') AS nature, COALESCE(p.strategy_id, '') AS strategy_id,
               COALESCE(p.close_reason, '') AS close_reason, COALESCE(p.entry_price, 0) AS entry_price,
               COALESCE(p.close_price, 0) AS close_price, COALESCE(p.size, 0) AS size,
               COALESCE(p.unrealized_pnl, 0) + COALESCE(p.partial_realized_pnl, 0) AS gross,
               COALESCE(p.partial_fee_paid, 0) AS partial_fee, p.status
        FROM paper_positions p
        LEFT JOIN LATERAL (
            SELECT id FROM trade_facts tf
            WHERE tf.position_id = p.id::text AND tf.account_id = p.account_id LIMIT 1
        ) f ON TRUE
        WHERE p.status IN ('closed', 'liquidated') AND p.closed_at IS NOT NULL AND p.closed_at >= :since
          AND f.id IS NULL{acct_clause}
        ORDER BY p.closed_at
        LIMIT :lim
        """
    ), params).mappings().all()
    stats = {"checked": len(rows), "inserted": 0, "errors": 0, "days": int(days)}
    for r in rows:
        try:
            tier = (r["tier"] or "").lower() or _NATURE_TO_TIER.get((r["nature"] or "").lower(), "mid")
            close_px = float(r["close_price"] or 0) or float(r["entry_price"] or 0)
            fees = float(r["size"] or 0) * close_px * rate + float(r["partial_fee"] or 0)
            gross = float(r["gross"] or 0)
            net = gross - fees
            outcome = "win" if net > 0 else ("loss" if net < 0 else "scratch")
            reason = str(r["close_reason"] or "")
            if str(r["status"]) == "liquidated" and "liquidat" not in reason.lower():
                reason = ("liquidation:" + reason)[:200]
            db.execute(_t(
                "INSERT INTO trade_facts (source, account_id, position_id, symbol, tier, side, entry_price, "
                " exit_price, fees, pnl, outcome, close_reason, factor_exposures, strategy_id, backfilled) "
                "VALUES ('paper', :a, :p, :s, :t, :d, :e, :x, :f, :pnl, :o, :r, NULL, :sid, TRUE)"
            ), {
                "a": int(r["account_id"]), "p": str(r["id"]), "s": str(r["symbol"]).upper(),
                "t": tier[:8], "d": str(r["side"] or "")[:8],
                "e": float(r["entry_price"] or 0), "x": close_px, "f": fees, "pnl": gross,
                "o": outcome, "r": reason[:200], "sid": str(r["strategy_id"] or "")[:64],
            })
            stats["inserted"] += 1
        except Exception as exc:
            stats["errors"] += 1
            logger.warning("[TradeFactsReconcile] 回填失败 pos=%s: %s", r["id"], exc)
            try:
                db.rollback()
            except Exception:
                pass
    try:
        db.commit()
    except Exception as exc:
        logger.warning("[TradeFactsReconcile] commit 失败: %s", exc)
        db.rollback()
    if stats["inserted"] or stats["errors"]:
        logger.info("[TradeFactsReconcile] %s", stats)
    return stats


def run_reconcile_job(days: int = 3) -> Dict[str, Any]:
    """定时任务入口（独立会话）。"""
    from backend.database.connection import SessionLocal
    with SessionLocal() as db:
        return backfill_missing_trade_facts(db, days=days)
