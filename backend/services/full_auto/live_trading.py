"""实盘宪法风控与下单 — 从 monolith 迁出（整改#8 Phase2）。"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


@dataclass
class LiveTradingHost:
    defensive_entered_at: Dict[str, float] = field(default_factory=dict)

    is_live_trading_session: Callable = field(repr=False, default=lambda *a, **k: False)
    is_unified_executor_on: Callable = field(repr=False, default=lambda: False)
    should_switch_mode: Callable = field(repr=False, default=lambda *a, **k: True)
    append_event: Callable = field(repr=False, default=lambda *a, **k: None)
    invalidate_session_status_cache: Callable = field(repr=False, default=lambda *a, **k: None)
    should_log_pause_event: Callable = field(repr=False, default=lambda *a, **k: True)


def build_live_trading_host(svc) -> LiveTradingHost:
    return LiveTradingHost(
        defensive_entered_at=svc._defensive_entered_at,
        is_live_trading_session=svc._is_live_trading_session,
        is_unified_executor_on=svc._is_unified_executor_on,
        should_switch_mode=svc._should_switch_mode,
        append_event=svc._append_event,
        invalidate_session_status_cache=svc._invalidate_session_status_cache,
        should_log_pause_event=svc._should_log_pause_event,
    )


def live_constitutional_enabled(session, host: LiveTradingHost) -> bool:
    if not host.is_live_trading_session(session):
        return False
    try:
        from backend.config.settings import LIVE_CONSTITUTIONAL_RISK_ENABLED
        return bool(LIVE_CONSTITUTIONAL_RISK_ENABLED)
    except Exception:
        return True

def fetch_live_account_snapshot(db: Session, account_id: int) -> dict:
    try:
        from backend.services.exchange.live_executor import LiveExecutor
        ex = LiveExecutor()
        bal = ex.get_balance(db, int(account_id)) or {}
        positions = ex.get_positions(db, int(account_id)) or []
        total_equity = float(
            bal.get("total_equity")
            or bal.get("equity")
            or bal.get("account_value")
            or 0
        )
        available = float(
            bal.get("available_balance")
            or bal.get("available")
            or bal.get("withdrawable")
            or 0
        )
        frozen = float(bal.get("frozen_margin") or bal.get("margin_used") or 0)
        margin_usage = 0.0
        if total_equity > 0 and frozen > 0:
            margin_usage = frozen / total_equity * 100.0
        return {
            "total_equity": total_equity,
            "available_balance": available,
            "margin_usage_percent": margin_usage,
            "positions": positions,
        }
    except Exception as err:
        logger.debug("[LiveConstitutional] snapshot 跳过: %s", err)
        return {}

def live_constitutional_pre_trade_check(
    db: Session, session, strat, decision: dict, host: LiveTradingHost,
) -> tuple:
    if not live_constitutional_enabled(session, host):
        return True, ""
    operation = (
        (decision.get("operation") or decision.get("action") or "")
        .strip()
        .lower()
    )
    if operation in ("close", "reduce", "hold", ""):
        return True, ""

    account_id = int(getattr(strat, "account_id", None) or session.account_id or 0)
    if not account_id:
        return False, "无有效实盘 account_id"

    snap = fetch_live_account_snapshot(db, account_id)
    total_equity = float(snap.get("total_equity") or 0)
    available_balance = float(snap.get("available_balance") or 0)
    positions = snap.get("positions") or []
    margin_usage = float(snap.get("margin_usage_percent") or 0)

    if total_equity <= 0:
        logger.warning(
            "[LiveConstitutional] 无法获取权益 account=%s，拒绝新开",
            account_id,
        )
        return False, "无法获取实盘权益，拒绝新开"

    symbol = str(
        decision.get("symbol") or getattr(strat, "primary_symbol", "") or ""
    ).upper()
    order_value = float(decision.get("order_value") or decision.get("notional") or 0)
    if order_value <= 0:
        pct = float(
            decision.get("position_pct")
            or decision.get("target_portion_of_balance")
            or 0.05
        )
        lev = float(decision.get("leverage") or 10)
        order_value = max(available_balance * pct * lev, 0.0)

    try:
        from backend.services.risk_control_service import check_risk_before_trade
        allowed, message = check_risk_before_trade(
            db=db,
            account_id=account_id,
            symbol=symbol,
            operation=operation if operation in ("buy", "sell") else "buy",
            order_value=order_value,
            total_equity=total_equity,
            available_balance=available_balance,
            positions=positions,
            margin_usage_percent=margin_usage,
        )
        return bool(allowed), str(message or "")
    except Exception as err:
        logger.error("[LiveConstitutional] 开单前检查异常: %s", err, exc_info=True)
        return False, f"宪法风控检查异常: {err}"

def check_live_constitutional_session_risk(
    db: Session, session, host: LiveTradingHost,
) -> None:
    if not live_constitutional_enabled(session, host):
        return
    account_id = int(getattr(session, "account_id", None) or 0)
    if not account_id:
        return
    snap = fetch_live_account_snapshot(db, account_id)
    equity = float(snap.get("total_equity") or 0)
    if equity <= 0:
        return
    try:
        from backend.services.risk_control_service import (
            RiskCheckResult,
            get_risk_control_service,
        )
        svc = get_risk_control_service()
        svc.load_config_from_db(db, account_id)
        resp = svc.check_daily_loss_breaker(db, account_id, equity)
        session_id = getattr(session, "session_id", "") or ""
        if resp.result == RiskCheckResult.BLOCKED:
            if session.status != "defensive":
                if not host.should_switch_mode(session_id, session.status, "defensive"):
                    logger.info("[LiveConstitutional] 进入防守被缓冲延迟 %s", session_id)
                    return
                host.defensive_entered_at[session_id] = time.time()
                host.append_event(
                    session,
                    "circuit_breaker",
                    f"[Live宪法] {resp.message}",
                )
                logger.warning(
                    "[LiveConstitutional] 进入防守 %s: %s",
                    session_id,
                    resp.message,
                )
                session.status = "defensive"
                session.pause_reason = "circuit_breaker"
                host.invalidate_session_status_cache(session_id)
        elif resp.result == RiskCheckResult.WARNING:
            if host.should_log_pause_event(session_id, "live_risk_warn"):
                host.append_event(
                    session,
                    "live_risk_warning",
                    f"[Live宪法] {resp.message}"[:200],
                )
    except Exception as err:
        logger.debug("[LiveConstitutional] 会话巡检跳过: %s", err)

def execute_live_trade(
    db: Session, session, strat, decision: dict, host: LiveTradingHost,
) -> None:
    try:
        _allowed, _risk_msg = live_constitutional_pre_trade_check(
            db, session, strat, decision, host
        )
        if not _allowed:
            symbol = decision.get("symbol", "?")
            operation = decision.get("operation") or decision.get("action", "?")
            logger.warning(
                "[LiveConstitutional] BLOCK %s %s: %s",
                symbol, operation, _risk_msg,
            )
            host.append_event(
                session,
                "live_risk_block",
                f"[Live宪法] {symbol} {operation} {_risk_msg[:100]}",
            )
            return

        # 同币已有仓 → 强制 adopt 交易所杠杆（一仓一杠杆），禁止另算覆盖
        try:
            from backend.services.leverage_authority import extract_existing_symbol_leverage
            _sym = str(
                decision.get("symbol") or getattr(strat, "primary_symbol", "") or ""
            ).upper()
            _acct = int(getattr(strat, "account_id", None) or session.account_id or 0)
            if _sym and _acct:
                _snap = fetch_live_account_snapshot(db, _acct)
                _adopt = extract_existing_symbol_leverage(_sym, _snap.get("positions") or [])
                if _adopt is not None:
                    _old = float(decision.get("leverage") or 0)
                    decision["leverage"] = float(_adopt)
                    if abs(_old - float(_adopt)) > 0.01:
                        logger.info(
                            "[LiveAdoptLev] %s %.1fx→%.1fx (existing exchange position)",
                            _sym, _old, float(_adopt),
                        )
        except Exception as _lev_err:
            logger.debug("[LiveAdoptLev] skip: %s", _lev_err)

        if host.is_unified_executor_on():
            # 统一执行器路径
            from backend.services.exchange.executors import OrderContext
            from backend.services.exchange.live_executor import LiveExecutor
            _ctx = OrderContext(
                account_id=strat.account_id,
                symbol=decision.get("symbol", strat.primary_symbol),
                side=decision.get("side", "buy" if decision.get("operation") == "buy" else "sell"),
                quantity=float(decision.get("quantity", 0) or 0),
                leverage=float(decision.get("leverage", 10) or 10),
                tp_price=decision.get("take_profit_price"),
                sl_price=decision.get("stop_loss_price"),
                strategy_id=strat.strategy_id,
                trade_nature=decision.get("trade_nature"),
                timeframe_tier=decision.get("timeframe_tier"),
                # 阶段 3.2: 执行算法透传（决策层可选产出 algo/algo_config）
                algo=decision.get("algo", "MARKET"),
                algo_config=decision.get("algo_config"),
                trigger_context={
                    "source": "full_auto",
                    "strategy_id": strat.strategy_id,
                    "pre_made_decisions": [decision],
                },
            )
            _ores = LiveExecutor().place_order(db, _ctx)
            symbol = decision.get("symbol", "?")
            operation = decision.get("operation", "?")
            if _ores.success:
                host.append_event(session, "live_trade",
                    f"实盘下单已提交(统一执行器): {symbol} {operation} status={_ores.status}")
            else:
                host.append_event(session, "live_trade_error",
                    f"实盘下单失败(统一执行器): {symbol} {operation} {_ores.error or _ores.status}")
            return

        # 原路径（默认）
        from backend.services.trading_commands import place_ai_driven_order

        trigger_ctx: Dict[str, Any] = {
            "source": "full_auto",
            "strategy_id": strat.strategy_id,
            "pre_made_decisions": [decision],
        }

        place_ai_driven_order(
            account_id=strat.account_id,
            trigger_context=trigger_ctx,
        )

        symbol = decision.get("symbol", "?")
        operation = decision.get("operation", "?")
        host.append_event(session, "live_trade",
            f"实盘下单已提交: {symbol} {operation}")
    except Exception as e:
        logger.error(f"[FullAuto] 实盘交易执行异常: {e}", exc_info=True)
        host.append_event(session, "live_trade_error", str(e)[:100])
