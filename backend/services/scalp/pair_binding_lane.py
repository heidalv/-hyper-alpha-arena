"""AI 选币绑定执行车道（候选 pass 且人工启用后才交易）。

对 pair_strategy_bindings 中 status='running' 的绑定：
- 按绑定参数（symbol/period/factor_set/threshold/sl/tp/max_hold）计算信号；
- 用专用纸盘账户（AI选币策略研究）开仓，strategy_id 隔离；
- 退出由 paper_engine.update_all_positions 统一管理（TP/SL/超时）；
- 决策写 kline_research_log（action='pair_opened'），心跳写 pair_binding_lane。

不做灰度：候选未 pass 无法 enable；enable 后自动受熔断监控。
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text

logger = logging.getLogger(__name__)

RESEARCH_ACCOUNT_NAME = "AI选币策略研究"
INITIAL_CAPITAL = 10000.0
RISK_PCT = 0.005

PERIOD_SEC = {"5m": 300, "1h": 3600, "4h": 14400}


def _enabled() -> bool:
    # 纠正「默认 true 却未调度」的谎言：未显式开启时禁止真实开仓
    return os.getenv("PAIR_BINDING_LANE_ENABLED", "false").lower() in ("1", "true", "yes", "on")


def _dry_run_snapshot() -> Dict[str, Any]:
    """调度心跳用：统计 running 绑定数，不开仓。"""
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.services.scalp.scalp_bindings import ensure_tables

    ensure_tables()
    n = 0
    with system_identity():
        with SessionLocal() as db:
            row = db.execute(
                text("SELECT count(*) AS n FROM pair_strategy_bindings WHERE status='running'")
            ).mappings().first()
            n = int(row["n"] or 0) if row else 0
    return {
        "enabled": False,
        "mode": "dry_run",
        "running_bindings": n,
        "trading": False,
        "note": "PAIR_BINDING_LANE_ENABLED=false：仅心跳，不开仓",
    }


def run_tick() -> Dict[str, Any]:
    """调度入口：默认干跑心跳；显式开启后才走 run_once 交易。"""
    if not _enabled():
        result = _dry_run_snapshot()
        try:
            from backend.services.scalp.scalp_heartbeat import touch
            touch("pair_binding_lane", "ok", {
                "mode": "dry_run",
                "running_bindings": result.get("running_bindings", 0),
                "trading": False,
            })
        except Exception:
            pass
        return result
    return run_once()


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
        " strategy_id VARCHAR(100),"
        " order_result JSONB)"
    ))
    db.execute(text(
        "ALTER TABLE kline_research_log ADD COLUMN IF NOT EXISTS strategy_id VARCHAR(100)"
    ))
    db.commit()


def _log(db, *, signal_ts, symbol, period, factor_set, composite, score, direction,
         action, reason="", entry_price=None, strategy_id=None, order_result=None) -> None:
    db.execute(
        text(
            "INSERT INTO kline_research_log "
            "(signal_ts, symbol, period, factor_set, composite, score, direction, threshold, "
            " action, reason, entry_price, strategy_id, order_result) "
            "VALUES (:ts, :sym, :period, :fs, :comp, :score, :dir, :thr, "
            " :action, :reason, :entry, :sid, :res)"
        ),
        {
            "ts": int(signal_ts),
            "sym": symbol,
            "period": period,
            "fs": factor_set,
            "comp": composite,
            "score": score,
            "dir": int(direction),
            "thr": 0.0,
            "action": action,
            "reason": str(reason)[:500],
            "entry": entry_price,
            "sid": strategy_id,
            "res": json.dumps(order_result or {}, ensure_ascii=False),
        },
    )
    db.commit()


def _ensure_account(db) -> Any:
    from backend.database.models import Account, PaperBalance

    acc = db.query(Account).filter(Account.name == RESEARCH_ACCOUNT_NAME).first()
    if acc is None:
        acc = Account(
            user_id=326, name=RESEARCH_ACCOUNT_NAME, account_type="PAPER",
            trading_mode="paper", selected_exchange="asterdex",
            initial_capital=INITIAL_CAPITAL, current_cash=INITIAL_CAPITAL,
            is_active="true", auto_trading_enabled="true", version="v1",
        )
        db.add(acc)
        db.flush()
        db.add(PaperBalance(
            account_id=acc.id, initial_balance=INITIAL_CAPITAL,
            total_equity=INITIAL_CAPITAL, available_balance=INITIAL_CAPITAL,
            realized_pnl=0.0,
        ))
        db.commit()
        logger.info("[PairLane] 创建专用账户 %s id=%s", RESEARCH_ACCOUNT_NAME, acc.id)
    bal = db.query(PaperBalance).filter(PaperBalance.account_id == acc.id).first()
    if bal is None:
        db.add(PaperBalance(
            account_id=acc.id, initial_balance=INITIAL_CAPITAL,
            total_equity=INITIAL_CAPITAL, available_balance=INITIAL_CAPITAL,
            realized_pnl=0.0,
        ))
        db.commit()
    return acc


def _running_bindings(db) -> List[Dict[str, Any]]:
    rows = db.execute(
        text(
            "SELECT id, symbol, period, factor_set, strategy_id, params_json "
            "FROM pair_strategy_bindings WHERE status = 'running' ORDER BY id"
        )
    ).mappings().all()
    out = []
    for r in rows:
        try:
            params = json.loads(r["params_json"]) if isinstance(r["params_json"], str) else (r["params_json"] or {})
        except Exception:
            params = {}
        out.append({**dict(r), "params": params})
    return out


def run_once() -> Dict[str, Any]:
    if not _enabled():
        return {"enabled": False}
    from backend.core.tenant import system_identity
    from backend.database.connection import SessionLocal
    from backend.database.models import PaperPosition
    from backend.services.paper_trading_engine import paper_engine
    from backend.services.scalp.kline_factor_backtest import (
        load_klines, compute_factors, composite_score,
    )

    now = datetime.now(timezone.utc)
    now_ts = int(now.timestamp())
    result: Dict[str, Any] = {"enabled": True, "checked_at": now.isoformat(), "bindings": []}
    with system_identity():
        with SessionLocal() as db:
            _ensure_log_table(db)
            account = _ensure_account(db)
            result["account_id"] = account.id
            for b in _running_bindings(db):
                entry: Dict[str, Any] = {"binding_id": b["id"], "symbol": b["symbol"],
                                         "period": b["period"], "factor_set": b["factor_set"]}
                try:
                    period_sec = PERIOD_SEC.get(b["period"], 3600)
                    p = b["params"] or {}
                    threshold = float(p.get("threshold", 0.5))
                    sl_pct = float(p.get("sl_pct", 0.01))
                    tp_pct = float(p.get("tp_pct", 0.02))
                    max_hold_hours = float(p.get("max_hold_candles", 12)) * period_sec / 3600.0
                    z_window = int(p.get("z_window", 120))
                    days = int(p.get("days", 180))
                    df = load_klines([b["symbol"]], days=days, exchange="asterdex",
                                     period=b["period"]).get(b["symbol"])
                    if df is None or len(df) < z_window + 10:
                        entry.update({"action": "skip_no_data"})
                        result["bindings"].append(entry)
                        continue
                    factors = compute_factors(df)
                    sig = composite_score(factors, z_window, b["factor_set"])
                    closed_idx = None
                    for i in range(len(df) - 1, -1, -1):
                        if int(df.index[i]) + period_sec <= now_ts:
                            closed_idx = i
                            break
                    if closed_idx is None:
                        entry.update({"action": "skip_no_closed_bar"})
                        result["bindings"].append(entry)
                        continue
                    signal_ts = int(df.index[closed_idx])
                    composite = float(sig["composite"].iloc[closed_idx])
                    score = float(sig["score"].iloc[closed_idx])
                    direction = 1 if composite > 0 else (-1 if composite < 0 else 0)
                    entry.update({"signal_ts": signal_ts, "composite": round(composite, 4),
                                  "score": round(score, 4), "direction": direction})
                    done = db.execute(
                        text(
                            "SELECT 1 FROM kline_research_log "
                            "WHERE signal_ts = :ts AND symbol = :sym AND strategy_id = :sid "
                            "AND action = 'pair_opened' LIMIT 1"
                        ),
                        {"ts": signal_ts, "sym": b["symbol"], "sid": b["strategy_id"]},
                    ).first()
                    if done:
                        entry.update({"action": "skip_already_processed"})
                        result["bindings"].append(entry)
                        continue
                    if abs(composite) < threshold or direction == 0:
                        _log(db, signal_ts=signal_ts, symbol=b["symbol"], period=b["period"],
                             factor_set=b["factor_set"], composite=composite, score=score,
                             direction=direction, action="pair_skip_no_signal",
                             strategy_id=b["strategy_id"])
                        entry.update({"action": "skip_no_signal"})
                        result["bindings"].append(entry)
                        continue
                    open_pos = db.query(PaperPosition).filter(
                        PaperPosition.account_id == account.id,
                        PaperPosition.symbol == b["symbol"],
                        PaperPosition.status == "open",
                        PaperPosition.strategy_id == b["strategy_id"],
                    ).first()
                    if open_pos:
                        entry.update({"action": "skip_position_open"})
                        result["bindings"].append(entry)
                        continue
                    price = float(df["close"].iloc[closed_idx])
                    equity = float((account.paper_balance.total_equity if account.paper_balance else 0) or INITIAL_CAPITAL)
                    risk_notional = equity * RISK_PCT / sl_pct if sl_pct > 0 else 0.0
                    max_notional = equity * float(
                        os.getenv("PAIR_BINDING_MAX_NOTIONAL_PCT", "0.20") or 0.20
                    )
                    notional = min(risk_notional, max_notional)
                    size = notional / price if price > 0 else 0.0
                    side = "buy" if direction > 0 else "sell"
                    tp_price = price * (1 + tp_pct) if direction > 0 else price * (1 - tp_pct)
                    sl_price = price * (1 - sl_pct) if direction > 0 else price * (1 + sl_pct)
                    order_result = paper_engine.place_order(
                        db=db, account_id=account.id, symbol=b["symbol"], side=side,
                        quantity=size, order_type="market", price=price, leverage=1.0,
                        tp_price=tp_price, sl_price=sl_price,
                        strategy_id=b["strategy_id"], timeframe_tier="research",
                        trade_nature="pair_research", expected_hold_hours=max_hold_hours,
                    )
                    db.commit()
                    ok = bool(order_result and not order_result.get("blocked")
                              and order_result.get("success", True))
                    action = "pair_opened" if ok else "pair_order_failed"
                    _log(db, signal_ts=signal_ts, symbol=b["symbol"], period=b["period"],
                         factor_set=b["factor_set"], composite=composite, score=score,
                         direction=direction, action=action,
                         reason=(json.dumps(order_result, ensure_ascii=False) if not ok else ""),
                         entry_price=price, strategy_id=b["strategy_id"],
                         order_result=order_result or {})
                    entry.update({"action": action, "order_result": order_result})
                except Exception as e:
                    entry.update({"action": "error", "reason": str(e)[:300]})
                result["bindings"].append(entry)
    try:
        from backend.services.scalp.scalp_heartbeat import touch
        touch("pair_binding_lane", "ok", {"bindings": len(result["bindings"])})
    except Exception:
        pass
    return result


if __name__ == "__main__":
    import sys
    rep = run_once()
    print(json.dumps(rep, ensure_ascii=False, indent=2, default=str))
    sys.exit(0)
