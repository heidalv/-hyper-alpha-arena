# -*- coding: utf-8 -*-
"""[P2 大轮回 2026-09-27] 出场后 60 分钟观察（§9.3）—— exit_markout 落库任务。

设计 §9.3：记录出场后价格路径，区分「躲过下跌（正确）」与「卖飞（过早）」，
每周汇总作为止损/止盈距离的唯一调参依据。

实现：
  - 表 `exit_markout`（position_id 主键，幂等）：exit_price / p_1h / p_4h /
    markout_1h_bp / markout_4h_bp。markout 符号语义：**多头** (p_after−exit)/exit×1e4，
    **空头** (exit−p_after)/exit×1e4 —— 正值 = 出场后价格朝被平方向继续走（卖飞/躲反），
    负值 = 出场躲对了。
  - 任务每 30 分钟：取近 48h 平仓且未落库的仓；1h 观察点等 closed_at+1h 后才写，
    4h 观察点等 +4h（数据未到时该列为 NULL，下一轮补）。
  - K 线源：alpha_market.crypto_klines（period='1h'，取 closed_at 目标时刻之后
    第一根 bar 的 close）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ensured = False
_ensure_lock = threading.Lock()


def ensure_table() -> None:
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        try:
            from sqlalchemy import text
            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    db.execute(text(
                        "CREATE TABLE IF NOT EXISTS exit_markout ("
                        " id BIGSERIAL PRIMARY KEY,"
                        " position_id INTEGER NOT NULL UNIQUE,"
                        " account_id INTEGER,"
                        " symbol VARCHAR(32),"
                        " side VARCHAR(8),"
                        " close_reason VARCHAR(100),"
                        " closed_at TIMESTAMPTZ,"
                        " exit_price DOUBLE PRECISION,"
                        " p_1h DOUBLE PRECISION,"
                        " markout_1h_bp DOUBLE PRECISION,"
                        " p_4h DOUBLE PRECISION,"
                        " markout_4h_bp DOUBLE PRECISION,"
                        " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                        " updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                        ")"
                    ))
                    db.commit()
            _ensured = True
        except Exception as exc:
            logger.warning("[ExitMarkout] 建表失败: %s", exc)


def _price_at_or_after(conn, symbol: str, ts: int) -> Optional[float]:
    """1h K 线中 timestamp ≥ ts 的第一根 bar close（±2 根内找最近）。"""
    from sqlalchemy import text
    rows = conn.execute(text("""
        SELECT timestamp, close_price FROM crypto_klines
        WHERE symbol = :sym AND period = '1h' AND timestamp >= :ts
        ORDER BY timestamp ASC LIMIT 1
    """), {"sym": symbol.upper(), "ts": ts}).fetchall()
    if rows and rows[0][1] is not None:
        return float(rows[0][1])
    return None


def _markout_bp(side: str, exit_price: float, p_after: Optional[float]) -> Optional[float]:
    if not p_after or exit_price <= 0:
        return None
    raw = (p_after - exit_price) / exit_price * 1e4
    return round(raw if str(side or "").lower().startswith("l") else -raw, 2)


def run_exit_markout() -> Dict[str, Any]:
    """30 分钟节奏任务：补写可计算的出场后观察点。"""
    ensure_table()
    from sqlalchemy import text
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal, market_engine

    out: Dict[str, Any] = {"rows": 0, "updated": 0, "error": None}
    try:
        with system_identity():
            with SessionLocal() as db:
                rows = db.execute(text("""
                    SELECT id, account_id, symbol, side, close_reason, closed_at,
                           close_price
                    FROM paper_positions
                    WHERE status='closed' AND closed_at >= now() - interval '48 hours'
                      AND close_price IS NOT NULL AND close_price > 0
                """)).fetchall()
        cands = [dict(r._mapping) for r in rows]
        with market_engine.connect() as mc:
            with system_identity():
                with SessionLocal() as db:
                    for p in cands:
                        pid = p["id"]
                        closed_ts = int(p["closed_at"].timestamp())
                        p1h = None
                        if time.time() >= closed_ts + 3600:
                            p1h = _price_at_or_after(mc, p["symbol"], closed_ts + 3600)
                        p4h = None
                        if time.time() >= closed_ts + 4 * 3600:
                            p4h = _price_at_or_after(mc, p["symbol"], closed_ts + 4 * 3600)
                        if p1h is None and p4h is None:
                            continue
                        out["rows"] += 1
                        db.execute(text("""
                            INSERT INTO exit_markout
                              (position_id, account_id, symbol, side, close_reason,
                               closed_at, exit_price, p_1h, markout_1h_bp,
                               p_4h, markout_4h_bp, updated_at)
                            VALUES (:pid,:acct,:sym,:side,:reason,:cat,:px,:p1,:m1,:p4,:m4, now())
                            ON CONFLICT (position_id) DO UPDATE SET
                              p_1h = COALESCE(EXCLUDED.p_1h, exit_markout.p_1h),
                              markout_1h_bp = COALESCE(EXCLUDED.markout_1h_bp, exit_markout.markout_1h_bp),
                              p_4h = COALESCE(EXCLUDED.p_4h, exit_markout.p_4h),
                              markout_4h_bp = COALESCE(EXCLUDED.markout_4h_bp, exit_markout.markout_4h_bp),
                              updated_at = now()
                        """), {
                            "pid": pid, "acct": p.get("account_id"), "sym": p["symbol"],
                            "side": p["side"], "reason": str(p.get("close_reason") or "")[:100],
                            "cat": p["closed_at"], "px": float(p["close_price"]),
                            "p1": p1h, "m1": _markout_bp(p["side"], float(p["close_price"]), p1h),
                            "p4": p4h, "m4": _markout_bp(p["side"], float(p["close_price"]), p4h),
                        })
                        out["updated"] += 1
                    db.commit()
    except Exception as exc:
        out["error"] = str(exc)[:200]
        logger.warning("[ExitMarkout] 失败: %s", exc)
    return out


def register() -> None:
    from backend.services.ops.job_registry import register_job
    register_job(
        "exit_markout_30m", "interval 1800s",
        "出场后 1h/4h 价格路径观察（§9.3 卖飞/躲过标记，exit_markout 幂等落库）",
        owner="p2", runner=run_exit_markout, expected_interval_sec=1800,
    )
