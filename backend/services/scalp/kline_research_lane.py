"""AAVE 4h 均值回归纸盘研究车道（M1 候选前向验证）。

独立于现有 scalp 车道：
- 专用 PAPER 账户（K线研究AAVE，$5000），不占用 scalp 账户资金/仓位；
- 固定策略：AAVE / 4h / meanrev / z窗口120 / 阈值0.5 / SL2% / TP4% / 持仓≤24h；
- 每 5 分钟检查一次最后一根已收盘 4h K 线，出信号就开一笔纸盘仓位；
- 退出（TP/SL/超时）由 paper_engine.update_all_positions 统一管理；
- 所有决策写入 kline_research_log，供 4-8 周后对照回测。

开关：KLINE_RESEARCH_ENABLED（默认 true）；开仓后同 signal_ts 不重复开。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

RESEARCH_ACCOUNT_NAME = "K线研究AAVE"
RESEARCH_STRATEGY_ID = "kline_research_aave_4h"
SYMBOL = "AAVE"
PERIOD = "4h"
PERIOD_SEC = 4 * 3600
EXCHANGE = "asterdex"
FACTOR_SET = "meanrev"
THRESHOLD = 0.5
Z_WINDOW = 120
SL_PCT = 0.02
TP_PCT = 0.04
MAX_HOLD_HOURS = 24.0
RISK_PCT = 0.005
INITIAL_CAPITAL = 5000.0
USER_ID = 326  # heida


def _enabled() -> bool:
    return os.getenv("KLINE_RESEARCH_ENABLED", "true").lower() in ("1", "true", "yes", "on")


def _ensure_log_table(db) -> None:
    db.execute(text(
        "CREATE TABLE IF NOT EXISTS kline_research_log ("
        " id BIGSERIAL PRIMARY KEY,"
        " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
        " signal_ts BIGINT,"
        " symbol VARCHAR(32),"
        " period VARCHAR(8),"
        " factor_set VARCHAR(16),"
        " composite DOUBLE PRECISION,"
        " score DOUBLE PRECISION,"
        " direction INT,"
        " threshold DOUBLE PRECISION,"
        " action VARCHAR(32),"
        " reason TEXT,"
        " entry_price DOUBLE PRECISION,"
        " order_result JSONB)"
    ))


def _log(db, *, signal_ts, symbol, composite, score, direction, action,
         reason="", entry_price=None, order_result=None) -> None:
    db.execute(
        text(
            "INSERT INTO kline_research_log "
            "(signal_ts, symbol, period, factor_set, composite, score, direction, threshold, "
            " action, reason, entry_price, order_result) "
            "VALUES (:ts, :sym, :period, :fs, :comp, :score, :dir, :thr, "
            " :action, :reason, :entry, :res)"
        ),
        {
            "ts": int(signal_ts),
            "sym": symbol,
            "period": PERIOD,
            "fs": FACTOR_SET,
            "comp": composite,
            "score": score,
            "dir": int(direction),
            "thr": THRESHOLD,
            "action": action,
            "reason": str(reason)[:500],
            "entry": entry_price,
            "res": json.dumps(order_result or {}, ensure_ascii=False),
        },
    )
    db.commit()


def ensure_research_account(db=None) -> Any:
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity
    from backend.database.models import Account, PaperBalance

    def _ensure(session) -> Any:
        acc = session.query(Account).filter(Account.name == RESEARCH_ACCOUNT_NAME).first()
        if acc is None:
            acc = Account(
                user_id=USER_ID,
                name=RESEARCH_ACCOUNT_NAME,
                account_type="PAPER",
                trading_mode="paper",
                selected_exchange=EXCHANGE,
                initial_capital=INITIAL_CAPITAL,
                current_cash=INITIAL_CAPITAL,
                is_active="true",
                auto_trading_enabled="true",
                version="v1",
            )
            session.add(acc)
            session.flush()
            session.add(PaperBalance(
                account_id=acc.id,
                initial_balance=INITIAL_CAPITAL,
                total_equity=INITIAL_CAPITAL,
                available_balance=INITIAL_CAPITAL,
                realized_pnl=0.0,
            ))
            session.commit()
            logger.info("[KlineResearch] 已创建专用纸盘账户 %s id=%s", RESEARCH_ACCOUNT_NAME, acc.id)
        bal = session.query(PaperBalance).filter(PaperBalance.account_id == acc.id).first()
        if bal is None:
            session.add(PaperBalance(
                account_id=acc.id,
                initial_balance=INITIAL_CAPITAL,
                total_equity=INITIAL_CAPITAL,
                available_balance=INITIAL_CAPITAL,
                realized_pnl=0.0,
            ))
            session.commit()
        return acc

    if db is not None:
        return _ensure(db)
    with system_identity():
        with SessionLocal() as session:
            return _ensure(session)


def run_once() -> Dict[str, Any]:
    if not _enabled():
        return {"enabled": False}
    from backend.database.connection import SessionLocal
    from backend.core.tenant import system_identity
    from backend.database.models import PaperPosition
    from backend.services.paper_trading_engine import paper_engine
    from backend.services.scalp.kline_factor_backtest import (
        load_klines, compute_factors, composite_score,
    )

    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    result: Dict[str, Any] = {"enabled": True, "checked_at": now.isoformat()}
    with system_identity():
        with SessionLocal() as db:
            account = ensure_research_account(db)
            _ensure_log_table(db)
            result["account_id"] = account.id
            try:
                df = load_klines([SYMBOL], days=45, exchange=EXCHANGE, period=PERIOD).get(SYMBOL)
                if df is None or len(df) < Z_WINDOW + 10:
                    result.update({"action": "skip_no_data", "reason": "K线不足"})
                    return result
                factors = compute_factors(df)
                sig = composite_score(factors, Z_WINDOW, FACTOR_SET)
                # 最后一根已收盘 4h K 线
                closed_idx = None
                for i in range(len(df) - 1, -1, -1):
                    if int(df.index[i]) + PERIOD_SEC <= now_ts:
                        closed_idx = i
                        break
                if closed_idx is None:
                    result.update({"action": "skip_no_closed_bar", "reason": "无已收盘K线"})
                    return result
                signal_ts = int(df.index[closed_idx])
                composite = float(sig["composite"].iloc[closed_idx])
                score = float(sig["score"].iloc[closed_idx])
                direction = 1 if composite > 0 else (-1 if composite < 0 else 0)
                result.update({
                    "signal_ts": signal_ts,
                    "composite": round(composite, 4),
                    "score": round(score, 4),
                    "direction": direction,
                })
                # 同信号已处理
                done = db.execute(
                    text(
                        "SELECT 1 FROM kline_research_log "
                        "WHERE signal_ts = :ts AND symbol = :sym AND action IN "
                        "('opened','order_failed','skip_no_signal','skip_position_open') LIMIT 1"
                    ),
                    {"ts": signal_ts, "sym": SYMBOL},
                ).first()
                if done:
                    result.update({"action": "skip_already_processed", "reason": "同信号已处理"})
                    return result
                if abs(composite) < THRESHOLD or direction == 0:
                    _log(db, signal_ts=signal_ts, symbol=SYMBOL, composite=composite,
                         score=score, direction=direction, action="skip_no_signal",
                         reason="|composite| < threshold")
                    result.update({"action": "skip_no_signal"})
                    return result
                open_pos = db.query(PaperPosition).filter(
                    PaperPosition.account_id == account.id,
                    PaperPosition.symbol == SYMBOL,
                    PaperPosition.status == "open",
                    PaperPosition.strategy_id == RESEARCH_STRATEGY_ID,
                ).first()
                if open_pos:
                    _log(db, signal_ts=signal_ts, symbol=SYMBOL, composite=composite,
                         score=score, direction=direction, action="skip_position_open",
                         reason="研究车道已有未平仓位")
                    result.update({"action": "skip_position_open"})
                    return result
                entry = float(df["close"].iloc[closed_idx])
                equity = float((account.paper_balance.total_equity if account.paper_balance else 0) or INITIAL_CAPITAL)
                notional = equity * RISK_PCT / SL_PCT
                size = notional / entry if entry > 0 else 0.0
                side = "buy" if direction > 0 else "sell"
                if direction > 0:
                    tp_price = entry * (1.0 + TP_PCT)
                    sl_price = entry * (1.0 - SL_PCT)
                else:
                    tp_price = entry * (1.0 - TP_PCT)
                    sl_price = entry * (1.0 + SL_PCT)
                order_result = paper_engine.place_order(
                    db=db,
                    account_id=account.id,
                    symbol=SYMBOL,
                    side=side,
                    quantity=size,
                    order_type="market",
                    price=entry,
                    leverage=1.0,
                    tp_price=tp_price,
                    sl_price=sl_price,
                    strategy_id=RESEARCH_STRATEGY_ID,
                    timeframe_tier="research",
                    trade_nature="research",
                    expected_hold_hours=MAX_HOLD_HOURS,
                )
                db.commit()
                ok = bool(
                    order_result
                    and not order_result.get("blocked")
                    and order_result.get("success", True)
                )
                action = "opened" if ok else "order_failed"
                _log(db, signal_ts=signal_ts, symbol=SYMBOL, composite=composite,
                     score=score, direction=direction, action=action,
                     reason=(json.dumps(order_result, ensure_ascii=False) if not ok else ""),
                     entry_price=entry, order_result=order_result or {})
                result.update({
                    "action": action,
                    "entry": round(entry, 6),
                    "side": side,
                    "size": round(size, 6),
                    "order_result": order_result,
                })
            except Exception as e:
                logger.exception("[KlineResearch] run_once 异常")
                result.update({"action": "error", "reason": str(e)[:300]})
    return result


if __name__ == "__main__":
    rep = run_once()
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    sys.exit(0)
