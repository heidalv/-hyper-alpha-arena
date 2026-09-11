"""
Trading Commands Service - Handles order execution and trading logic
"""
import logging
import random
import time
import uuid
from decimal import Decimal
from typing import Dict, Optional, Tuple, List, Iterable, Any

from sqlalchemy.orm import Session
from sqlalchemy import text, func

from backend.database.connection import SessionLocal
from backend.database.models import (
    Position,
    Account,
    CRYPTO_MIN_COMMISSION,
    CRYPTO_COMMISSION_RATE,
)
from backend.services.asset_calculator import calc_positions_value
from backend.services.market_data import get_last_price
from backend.services.order_matching import create_order, check_and_execute_order
from backend.services.ai_decision_service import (
    call_ai_for_decision,
    save_ai_decision,
    get_active_ai_accounts,
    _get_portfolio_data,
    SUPPORTED_SYMBOLS,
    resolve_account_llm_config,
)
from backend.database.models import SystemConfig
from backend.services.hyperliquid_symbol_service import (
    get_selected_symbols as get_hyperliquid_selected_symbols,
    get_available_symbol_map as get_hyperliquid_symbol_map,
    get_symbol_display as get_hyperliquid_symbol_display,
)
from backend.services.risk_control_service import (
    check_risk_before_trade,
    get_risk_control_service,
    RiskCheckResult,
)


def get_ai_trading_symbols() -> List[str]:
    from backend.services.trading_pairs_config import get_user_trading_pairs
    return get_user_trading_pairs()


# FIX-3：main.py / scheduler 等仍 `from ... import AI_TRADING_SYMBOLS`
AI_TRADING_SYMBOLS: List[str] = list(SUPPORTED_SYMBOLS.keys())

logger = logging.getLogger(__name__)
ORACLE_PRICE_DEVIATION_LIMIT_PERCENT = 1.0


# ══════════════════════════════════════════════════
#  AI Decision Validation & Auto-Fill
# ══════════════════════════════════════════════════

def _normalize_confidence(raw_conf: Any) -> float:
    """Normalize confidence to 0.0-1.0 range, handling 0-100 and edge cases."""
    try:
        conf = float(raw_conf)
        if conf > 1.0:
            conf = conf / 100.0
        if conf < 0.0:
            conf = 0.0
        if conf > 1.0:
            conf = 1.0
        return conf
    except (TypeError, ValueError):
        return 0.5  # Default to moderate confidence


def _live_maker_first_decision(
    exchange: str, decision: Dict, trigger_context: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, float]:
    """[2026-09-03 Aster 费率优化] 该笔实盘开仓是否走 maker 优先，返回 (bool, timeout_s)。

    门控全部在 live_points_engine.maker_first_for：仅 asterdex + 开仓(非 reduce_only)
    + 周期 ∈ 交易所配置的 maker_first_tiers（默认 mid/long）+ 配置页开关开。
    周期来源优先级：trigger_context["tier"]（全自动/中长线调度写入）→
    decision["timeframe_tier"]/["tier"]（AI 输出）→ 无周期 = 不走 maker（宁可市价）。
    任何异常一律返回 (False, 0) → 原样市价，绝不因为优化逻辑影响正常交易。
    """
    try:
        if str(exchange or "").lower() != "asterdex":
            return False, 0.0
        if bool(decision.get("reduce_only")):
            return False, 0.0
        _tier = (
            ((trigger_context or {}).get("tier") if isinstance(trigger_context, dict) else None)
            or decision.get("timeframe_tier")
            or decision.get("tier")
        )
        from backend.services.rebate_arb.live_points_engine import live_points_engine
        return live_points_engine.maker_first_for(
            exchange, timeframe_tier=_tier, is_open=True,
        )
    except Exception as _mf_err:
        logger.debug("[MakerFirst] gate error → taker: %s", _mf_err)
        return False, 0.0


def _place_order_fresh_client(
    mgr, exchange: str, user_id: int, account_id: int, market_type, order,
    *, timeout_sec: float = 20.0, maker_first: bool = False, maker_timeout_s: float = 30.0,
):
    """[2026-08-31 挂死根治] 每次下单新建客户端 + wait_for 硬超时 + 用完即关。

    背景（trading_commands 8/29 注释已定位、但只修了一半）：ccxt async 的
    aiohttp 会话绑定在首次调用所在的事件循环上；本函数调用方流程是
    asyncio.run(余额/持仓) 之后再 asyncio.run(下单) —— 同一客户端第二次
    进新循环会【永久挂起】（不是抛错）。入口处 create_fresh_client 只保证
    跨调用隔离，不解决一次调用内的多次 asyncio.run。故所有下单点统一走
    本 helper：新客户端、单次 run、20s 硬超时、诚实异常。

    [2026-09-03] maker_first=True 且客户端支持 place_order_maker_first（asterdex）时，
    先 post-only 挂最优价等 maker_timeout_s 秒，未成交自动撤单回退市价；硬超时相应
    放宽为 maker_timeout_s + timeout_sec，保证兜底市价单有完整时间窗。

    [2026-09-04 p2-oms-exec] 下单前挂 client_order_id 并落 live_orders intent；
    EXEC_ALGO_ENABLED=true 时改走通用 maker 追价（失败自动回退本函数原路径）。
    """
    import asyncio as _aio

    # ── OMS：幂等键 + 可选 ExecutionAlgo ──
    _oms_cid = None
    try:
        from backend.services.oms.bridge import begin_order, finish_order, try_algo_place

        _oms_cid = begin_order(order, account_id=account_id, exchange=exchange)
    except Exception as _oms_begin_err:
        logger.debug("[OMS] begin_order skip: %s", _oms_begin_err)
        begin_order = finish_order = try_algo_place = None  # type: ignore

    _client = mgr.create_fresh_client(
        exchange, user_id=user_id, account_id=account_id,
        market_type=market_type if exchange == "binance" else None,
    )
    if _client is None:
        if finish_order and _oms_cid:
            finish_order(_oms_cid, None, error="fresh client 创建失败")
        raise RuntimeError(f"{exchange} fresh client 创建失败(凭证缺失?)")

    # ExecutionAlgo 接管（默认关）
    if try_algo_place is not None:
        try:
            _algo_res = try_algo_place(_client, order, account_id=account_id, exchange=exchange)
            if _algo_res is not None:
                try:
                    import asyncio as _aio_close
                    _aio_close.get_event_loop().run_until_complete(_client.close())
                except Exception:
                    try:
                        _aio.run(_client.close())
                    except Exception:
                        pass
                if str(_algo_res.get("status") or "").lower() == "error":
                    raise RuntimeError(str(_algo_res.get("message") or _algo_res)[:200])
                _v3_connectivity_record(exchange, ok=True)
                return _algo_res
        except RuntimeError:
            raise
        except Exception as _algo_err:
            logger.warning("[OMS] algo 回退原路径: %s", _algo_err)

    _use_mf = bool(maker_first) and hasattr(_client, "place_order_maker_first")
    _hard_timeout = float(timeout_sec) + (float(maker_timeout_s) if _use_mf else 0.0)

    async def _run():
        try:
            if _use_mf:
                logger.info(
                    "[MakerFirst] %s %s %s qty=%s → post-only 挂单 %.0fs 后回退市价",
                    exchange, getattr(order, "symbol", "?"),
                    getattr(getattr(order, "side", None), "value", getattr(order, "side", "?")),
                    getattr(order, "size", "?"), float(maker_timeout_s),
                )
                _res = await _aio.wait_for(
                    _client.place_order_maker_first(order, timeout_s=float(maker_timeout_s)),
                    timeout=_hard_timeout,
                )
            else:
                _res = await _aio.wait_for(_client.place_order(order), timeout=timeout_sec)
        finally:
            try:
                await _client.close()
            except Exception:
                pass
        # 适配器对交易所拒单返回 {"status":"error"} 而不抛异常——不检查会把
        # 拒单当成交记 executed=True（实测 -1106 reduceOnly 拒单被记成功）。
        if isinstance(_res, dict) and str(_res.get("status") or "").lower() == "error":
            raise RuntimeError(str(_res.get("message") or _res)[:200])
        return _res

    # [2026-09-03 v3 方向7] 连通性熔断计数：超时/连接类异常记失败（连续 ≥N 该 venue 禁开），
    # 成功或“交易所明确拒单”记成功（拒单说明链路是通的）。
    try:
        _res_final = _aio.run(_run())
    except (_aio.TimeoutError, TimeoutError, ConnectionError, OSError) as _conn_err:
        _v3_connectivity_record(exchange, ok=False, error=str(_conn_err))
        if finish_order and _oms_cid:
            # 超时不能猜成败 → unknown，交给对账
            try:
                from backend.services.oms.order_store import OrderStatus, transition
                transition(_oms_cid, OrderStatus.UNKNOWN, error=f"timeout: {_conn_err}"[:400])
            except Exception:
                finish_order(_oms_cid, None, error=str(_conn_err)[:400])
        raise
    except Exception as _other_err:
        _msg = str(_other_err).lower()
        _is_conn = any(k in _msg for k in ("timeout", "timed out", "connection", "network", "temporarily unavailable",
                                            "service unavailable", "502", "503", "504", "dns", "reset by peer"))
        _v3_connectivity_record(exchange, ok=not _is_conn, error=str(_other_err))
        if finish_order and _oms_cid:
            finish_order(_oms_cid, None, error=str(_other_err)[:400])
        raise
    _v3_connectivity_record(exchange, ok=True)
    if finish_order and _oms_cid:
        finish_order(_oms_cid, _res_final)
    return _res_final


def _v3_connectivity_record(exchange: str, *, ok: bool, error: str = "") -> None:
    try:
        from backend.services.risk.circuit_breakers import get_connectivity_breaker
        if ok:
            get_connectivity_breaker().record_success(exchange)
        else:
            get_connectivity_breaker().record_failure(exchange, error)
    except Exception as _cb_err:  # pragma: no cover
        logger.debug("[RiskEngine v3] connectivity record fail: %s", _cb_err)


def _v3_risk_pre_trade_live(
    db, account, symbol: str, operation: str, *, order_value: float, total_equity: float,
    leverage: float, exchange: str, decision: Optional[Dict] = None,
) -> Optional[str]:
    """[2026-09-03 v3 方向7] 实盘开仓前 RiskEngine 单入口。返回拦截原因（None=放行）。

    覆盖：LIVE_KILL_SWITCH / TradingState / 该 venue 连通性熔断 / 事件避险窗口 / 日开仓配额（单一来源）。
    引擎自身异常 → 放行并告警日志（状态判定基于已落盘状态，不会因数据缺失误放行）。
    """
    try:
        from backend.services.risk.risk_engine import pre_trade as _v3_pre_trade, PreTradeRequest as _V3Req
        _d = decision or {}
        _verdict = _v3_pre_trade(
            db,
            _V3Req(
                account_id=int(getattr(account, "id", 0) or 0), symbol=str(symbol), side=str(operation),
                is_open=True, venue=str(exchange or "").lower(),
                tier=_d.get("timeframe_tier") or _d.get("tier"),
                trade_nature=_d.get("trade_nature") or _d.get("nature"),
                notional=float(order_value or 0), equity=float(total_equity or 0), leverage=float(leverage or 1),
                source="live_ai_trade",
            ),
        )
        if not _verdict.allowed:
            logger.warning("[RiskEngine v3][%s] 拦截 %s %s: %s", str(exchange).upper(), symbol, operation, _verdict.reason)
            return f"RiskEngine: {_verdict.reason}"
        return None
    except Exception as _v3_err:
        logger.warning("[RiskEngine v3] live 检查异常（放行）: %s", _v3_err)
        return None


def _v3_position_construction_live(
    db, account, symbol: str, *, price: float, order_value: float, leverage: float, total_equity: float,
    decision: Optional[Dict] = None, exchange: str = "",
) -> Tuple[float, float, float, Optional[str]]:
    """[2026-09-03 v3 方向1] 实盘开仓名义/杠杆经 PositionConstruction 单一权威夹紧。

    返回 (order_value, leverage, quantity, block_reason)。block_reason 非空 = 该币/簇/车道已无余量，应拒单。
    异常放行（返回原值）。上游 AI 的 target_portion × leverage 从此只是"提议"。
    """
    try:
        from backend.services import position_construction as _pc
        if not _pc.enforce_enabled() or not price or price <= 0 or order_value <= 0:
            return order_value, leverage, round(order_value / price, 6) if price else 0.0, None
        _d = decision or {}
        _lane = _pc.normalize_lane(_d.get("timeframe_tier") or _d.get("tier"), _d.get("trade_nature") or _d.get("nature"))
        _open = _pc.open_notionals(db, int(getattr(account, "id", 0) or 0), symbol, lane=_lane)
        _sd = None
        _sl = _d.get("stop_loss") or _d.get("sl_price")
        try:
            if _sl and float(_sl) > 0:
                _sd = abs(float(price) - float(_sl)) / float(price)
        except Exception:
            _sd = None
        _cl = _pc.clamp(
            lane=_lane, symbol=symbol, equity=float(total_equity or 0), price=float(price),
            notional=float(order_value), leverage=float(leverage or 1.0), stop_distance_pct=_sd,
            symbol_open_notional=_open["symbol"], cluster_open_notional=_open["cluster"], lane_open_notional=_open["lane"],
        )
        if _cl.blocked:
            logger.warning("[PositionConstruction][%s] 拒单 %s lane=%s: %s", str(exchange).upper(), symbol, _lane, _cl.reason)
            return order_value, leverage, 0.0, f"PositionConstruction: {_cl.reason}"
        if _cl.changed:
            logger.info("[PositionConstruction][%s] %s lane=%s notional %.2f→%.2f lev %s→%s caps=%s",
                        str(exchange).upper(), symbol, _lane, order_value, _cl.notional, leverage, _cl.leverage, _cl.caps_applied)
            if isinstance(decision, dict):
                decision["position_construction"] = {"caps": _cl.caps_applied, "lane": _lane, "notional": _cl.notional,
                                                     "leverage": _cl.leverage}
        return float(_cl.notional), float(_cl.leverage), round(float(_cl.quantity), 6), None
    except Exception as _pc_err:
        logger.warning("[PositionConstruction] live 检查异常（放行）: %s", _pc_err)
        return order_value, leverage, round(order_value / price, 6) if price else 0.0, None


def _validate_and_autofill_decision(
    decision: Dict,
    current_prices: Dict[str, float],
    symbol_whitelist: set,
) -> Tuple[Dict, List[str]]:
    """Validate AI decision and auto-fill missing required fields.

    Returns:
        (decision, warnings) - patched decision dict and list of warning messages
    """
    warnings = []
    operation = decision.get("operation", "").lower()
    symbol = decision.get("symbol", "").upper()
    current_price = current_prices.get(symbol, 0)

    # Validate symbol
    if symbol not in symbol_whitelist:
        warnings.append(f"Symbol '{symbol}' not in whitelist")
        return decision, warnings

    if operation not in ("buy", "sell", "hold", "close"):
        warnings.append(f"Invalid operation '{operation}'")
        return decision, warnings

    if operation == "hold":
        return decision, warnings

    if current_price <= 0:
        warnings.append(f"No market price for {symbol}")
        return decision, warnings

    # Auto-fill max_price for BUY
    if operation == "buy" and not decision.get("max_price"):
        decision["max_price"] = current_price * 1.005  # 0.5% above market
        warnings.append(f"Auto-filled max_price={decision['max_price']:.2f} for BUY {symbol}")

    # Auto-fill min_price for SELL
    if operation == "sell" and not decision.get("min_price"):
        decision["min_price"] = current_price * 0.995  # 0.5% below market
        warnings.append(f"Auto-filled min_price={decision['min_price']:.2f} for SELL {symbol}")

    # Auto-fill min_price for CLOSE long, max_price for CLOSE short
    if operation == "close":
        if not decision.get("min_price"):
            decision["min_price"] = current_price * 0.995
            warnings.append(f"Auto-filled min_price={decision['min_price']:.2f} for CLOSE {symbol}")
        if not decision.get("max_price"):
            decision["max_price"] = current_price * 1.005
            warnings.append(f"Auto-filled max_price={decision['max_price']:.2f} for CLOSE {symbol}")

    # Auto-fill TP/SL for buy/sell
    if operation in ("buy", "sell"):
        if not decision.get("take_profit_price"):
            tp_pct = 0.05  # Default 5% TP
            if operation == "buy":
                decision["take_profit_price"] = round(current_price * (1 + tp_pct), 2)
            else:
                decision["take_profit_price"] = round(current_price * (1 - tp_pct), 2)
            warnings.append(f"Auto-filled take_profit_price={decision['take_profit_price']:.2f} for {operation} {symbol}")

        if not decision.get("stop_loss_price"):
            sl_pct = 0.03  # Default 3% SL
            if operation == "buy":
                decision["stop_loss_price"] = round(current_price * (1 - sl_pct), 2)
            else:
                decision["stop_loss_price"] = round(current_price * (1 + sl_pct), 2)
            warnings.append(f"Auto-filled stop_loss_price={decision['stop_loss_price']:.2f} for {operation} {symbol}")

    # Validate TP/SL direction consistency
    if operation == "buy":
        tp = decision.get("take_profit_price")
        sl = decision.get("stop_loss_price")
        if tp and tp <= current_price:
            warnings.append(f"TP {tp} <= current {current_price} for BUY, adjusting")
            decision["take_profit_price"] = round(current_price * 1.05, 2)
        if sl and sl >= current_price:
            warnings.append(f"SL {sl} >= current {current_price} for BUY, adjusting")
            decision["stop_loss_price"] = round(current_price * 0.97, 2)
    elif operation == "sell":
        tp = decision.get("take_profit_price")
        sl = decision.get("stop_loss_price")
        if tp and tp >= current_price:
            warnings.append(f"TP {tp} >= current {current_price} for SELL, adjusting")
            decision["take_profit_price"] = round(current_price * 0.95, 2)
        if sl and sl <= current_price:
            warnings.append(f"SL {sl} <= current {current_price} for SELL, adjusting")
            decision["stop_loss_price"] = round(current_price * 1.03, 2)

    return decision, warnings


# ══════════════════════════════════════════════════
#  Close Cooldown Manager - Prevent over-closing
# ══════════════════════════════════════════════════

class CloseCooldownManager:
    """Prevent rapid successive close operations on the same symbol."""

    def __init__(self, cooldown_seconds: int = 300):
        self._last_close_time: Dict[str, float] = {}
        self._cooldown_seconds = cooldown_seconds

    def can_close(self, account_id: int, symbol: str) -> Tuple[bool, float]:
        """Check if close is allowed (not in cooldown).

        Returns:
            (allowed, remaining_seconds)
        """
        key = f"{account_id}_{symbol}"
        last_close = self._last_close_time.get(key, 0)
        elapsed = time.time() - last_close
        remaining = max(0, self._cooldown_seconds - elapsed)
        return elapsed >= self._cooldown_seconds, remaining

    def mark_closed(self, account_id: int, symbol: str):
        """Mark that a close operation was just performed."""
        key = f"{account_id}_{symbol}"
        self._last_close_time[key] = time.time()

    def reset(self, account_id: int, symbol: str):
        """Reset cooldown for a specific symbol (e.g., after a new position is opened)."""
        key = f"{account_id}_{symbol}"
        self._last_close_time.pop(key, None)


_close_cooldown_manager = CloseCooldownManager(cooldown_seconds=300)  # 5-minute cooldown


def _enforce_price_bounds(
    *,
    symbol: str,
    account_name: str,
    operation: str,
    current_price: float,
    requested_price: float,
) -> Tuple[float, float, bool]:
    """Clamp requested price into ±1% oracle window and log adjustments."""

    if current_price <= 0 or requested_price <= 0:
        return requested_price, 0.0, False

    limit = ORACLE_PRICE_DEVIATION_LIMIT_PERCENT / 100
    lower_bound = current_price * (1 - limit)
    upper_bound = current_price * (1 + limit)

    clamped_price = max(min(requested_price, upper_bound), lower_bound)
    deviation_percent = abs(requested_price - current_price) / current_price * 100
    was_adjusted = clamped_price != requested_price

    if was_adjusted:
        logger.warning(
            f"[AI COMPLIANCE] {operation.upper()} {symbol} price from AI for {account_name} "
            f"violates Hyperliquid ±1% rule. market=${current_price:.2f}, "
            f"requested=${requested_price:.2f}, deviation={deviation_percent:.2f}%. "
            f"Adjusted to ${clamped_price:.2f}."
        )

    return clamped_price, deviation_percent, was_adjusted


def _get_symbol_name(symbol: str) -> str:
    return SUPPORTED_SYMBOLS.get(symbol, symbol)


def _estimate_buy_cash_needed(price: float, quantity: float) -> Decimal:
    """Estimate cash required for a BUY including commission."""
    notional = Decimal(str(price)) * Decimal(str(quantity))
    commission = max(
        notional * Decimal(str(CRYPTO_COMMISSION_RATE)),
        Decimal(str(CRYPTO_MIN_COMMISSION)),
    )
    return notional + commission


def _get_market_prices(symbols: List[str]) -> Dict[str, float]:
    """Get latest prices for given symbols"""
    prices = {}
    for symbol in symbols:
        try:
            price = float(get_last_price(symbol, "CRYPTO"))
            if price > 0:
                prices[symbol] = price
        except Exception as err:
            logger.warning(f"Failed to get price for {symbol}: {err}")
    return prices


def _select_side(db: Session, account: Account, symbol: str, max_value: float) -> Optional[Tuple[str, int]]:
    """Select random trading side and quantity for legacy random trading"""
    market = "CRYPTO"
    try:
        price = float(get_last_price(symbol, market))
    except Exception as err:
        logger.warning("Cannot get price for %s: %s", symbol, err)
        return None

    if price <= 0:
        logger.debug("%s returned non-positive price %s", symbol, price)
        return None

    max_quantity_by_value = int(Decimal(str(max_value)) // Decimal(str(price)))
    position = (
        db.query(Position)
        .filter(Position.account_id == account.id, Position.symbol == symbol, Position.market == market)
        .first()
    )
    available_quantity = int(position.available_quantity) if position else 0

    choices = []

    if float(account.current_cash) >= price and max_quantity_by_value >= 1:
        choices.append(("BUY", max_quantity_by_value))

    if available_quantity > 0:
        max_sell_quantity = min(available_quantity, max_quantity_by_value if max_quantity_by_value >= 1 else available_quantity)
        if max_sell_quantity >= 1:
            choices.append(("SELL", max_sell_quantity))

    if not choices:
        return None

    side, max_qty = random.choice(choices)
    quantity = random.randint(1, max_qty)
    return side, quantity


def place_ai_driven_crypto_order(max_ratio: float = 0.2, account_ids: Optional[Iterable[int]] = None, account_id: Optional[int] = None, symbol: Optional[str] = None, samples: Optional[List] = None) -> None:
    """Place crypto order based on AI model decision.

    Args:
        max_ratio: maximum portion of portfolio to allocate per trade.
        account_ids: optional iterable of account IDs to process (defaults to all active accounts).
    """
    db = SessionLocal()
    try:
        # Handle single account strategy trigger
        if account_id is not None:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account or account.is_active != "true" or account.auto_trading_enabled != "true":
                logger.debug(f"Account {account_id} not found, inactive, or auto trading disabled, skipping AI trading")
                return
            accounts = [account]
        else:
            accounts = get_active_ai_accounts(db)
            if not accounts:
                logger.debug("No available accounts, skipping AI trading")
                return

            if account_ids is not None:
                id_set = {int(acc_id) for acc_id in account_ids}
                accounts = [acc for acc in accounts if acc.id in id_set]
                if not accounts:
                    logger.debug("No matching accounts for provided IDs: %s", account_ids)
                    return

        # Get latest market prices once for all accounts
        prices = _get_market_prices(get_ai_trading_symbols())
        if not prices:
            logger.warning("Failed to fetch market prices, skipping AI trading")
            return

        # Get all symbols with available sampling data
        from services.sampling_pool import sampling_pool
        available_symbols = []
        for sym in SUPPORTED_SYMBOLS.keys():
            samples_data = sampling_pool.get_samples(sym)
            if samples_data:
                available_symbols.append(sym)

        if available_symbols:
            logger.info(f"Available sampling pool symbols: {', '.join(available_symbols)}")
        else:
            logger.warning("暂无任何交易对的采样数据")

        # Iterate through all active accounts
        for account in accounts:
            try:
                logger.info(f"Processing AI trading for account: {account.name}")

                # All accounts now use Hyperliquid trading pipeline
                logger.info(f"Processing Hyperliquid trading for account {account.name}")
                place_ai_driven_hyperliquid_order(account_id=account.id)

            except Exception as account_err:
                logger.error(f"AI-driven order placement failed for account {account.name}: {account_err}", exc_info=True)
                # Continue with next account even if one fails

    except Exception as err:
        logger.error(f"AI-driven order placement failed: {err}", exc_info=True)
        db.rollback()
    finally:
        db.close()


def place_random_crypto_order(max_ratio: float = 0.2) -> None:
    """Legacy random order placement (kept for backward compatibility)"""
    db = SessionLocal()
    try:
        accounts = get_active_ai_accounts(db)
        if not accounts:
            logger.debug("No available accounts, skipping auto order placement")
            return
        
        # For legacy compatibility, just pick a random account from the list
        account = random.choice(accounts)

        positions_value = calc_positions_value(db, account.id)
        total_assets = positions_value + float(account.current_cash)

        if total_assets <= 0:
            logger.debug("Account %s total assets non-positive, skipping auto order placement", account.name)
            return

        max_order_value = total_assets * max_ratio
        if max_order_value <= 0:
            logger.debug("Account %s maximum order amount is 0, skipping", account.name)
            return

        symbol = random.choice(list(SUPPORTED_SYMBOLS.keys()))
        side_info = _select_side(db, account, symbol, max_order_value)
        if not side_info:
            logger.debug("Account %s has no executable direction for %s, skipping", account.name, symbol)
            return

        side, quantity = side_info
        name = _get_symbol_name(symbol)

        order = create_order(
            db=db,
            account=account,
            symbol=symbol,
            name=name,
            side=side,
            order_type="MARKET",
            price=None,
            quantity=quantity,
        )

        db.commit()
        db.refresh(order)

        executed = check_and_execute_order(db, order)
        if executed:
            db.refresh(order)
            logger.info("Auto order executed: account=%s %s %s %s quantity=%s", account.name, side, symbol, order.order_no, quantity)
        else:
            logger.info("Auto order created: account=%s %s %s quantity=%s order_id=%s", account.name, side, symbol, quantity, order.order_no)

    except Exception as err:
        logger.error("Auto order placement failed: %s", err)
        db.rollback()
    finally:
        db.close()


AUTO_TRADE_JOB_ID = "auto_crypto_trade"
AI_TRADE_JOB_ID = "ai_crypto_trade"


def test_hyperliquid_function():
    return "test_success"


def _place_hl_order_with_algo(
    client,
    decision: Optional[dict],
    *,
    db,
    symbol: str,
    is_buy: bool,
    quantity: float,
    price: float,
    leverage: int,
    time_in_force: str,
    take_profit_price,
    stop_loss_price,
    is_cross: bool,
) -> Optional[dict]:
    """Hyperliquid 下单（阶段 3.2 OrderAlgo 接线）。

    - MARKET（默认）: 单笔直下（原行为不变）
    - TWAP / FUNDING_IS: 切片循环；仅最后一片带 TP/SL（避免重复触发单）
    - POV: 无实时成交量 → 降级 TWAP（日志告警）
    - SOR: 单 venue → 单笔（日志告警）
    """
    algo = str((decision or {}).get("algo", "MARKET") or "MARKET").upper()
    algo_config = (decision or {}).get("algo_config")
    quantity = float(quantity or 0)

    if algo == "MARKET" or quantity <= 0:
        return client.place_order_with_tpsl(
            db=db, symbol=symbol, is_buy=is_buy, size=quantity, price=price,
            leverage=leverage, time_in_force=time_in_force, reduce_only=False,
            take_profit_price=take_profit_price,
            stop_loss_price=stop_loss_price, is_cross=is_cross,
        )

    from backend.services.exchange.algo_exec import build_algo_slices, execute_slices

    children, meta = build_algo_slices(quantity, algo, algo_config)
    if not children:
        logger.warning(f"[AlgoExec][HL:{algo}] {symbol} 切片为空，跳过")
        return {"status": "error", "error": f"algo {algo} 切片为空"}
    if meta.get("fallback"):
        logger.warning(
            f"[AlgoExec][HL:{algo}] {symbol} {'BUY' if is_buy else 'SELL'} 降级: {meta['fallback']}"
        )

    def _place_slice(qty: float, is_last: bool) -> Optional[dict]:
        # 仅最后一片携带 TP/SL（避免多组触发单重复平仓）
        _tp = take_profit_price if is_last else None
        _sl = stop_loss_price if is_last else None
        r = client.place_order_with_tpsl(
            db=db, symbol=symbol, is_buy=is_buy, size=qty, price=price,
            leverage=leverage, time_in_force=time_in_force, reduce_only=False,
            take_profit_price=_tp, stop_loss_price=_sl, is_cross=is_cross,
        )
        logger.info(
            f"[AlgoExec][HL:{algo}] slice {'LAST' if is_last else '..'} "
            f"{symbol} {'BUY' if is_buy else 'SELL'} qty={qty} tp={_tp} sl={_sl} "
            f"-> {r.get('status') if isinstance(r, dict) else r}"
        )
        return r

    out = execute_slices(children, _place_slice, log_prefix=f"[AlgoExec][HL:{algo}]",
                         sleep_fn=time.sleep)
    results = [r for r in out["results"] if r is not None]
    if not results:
        return {"status": "error", "error": f"algo {algo} 全部子单失败: {out['errors']}"}
    merged = dict(results[-1]) if isinstance(results[-1], dict) else {"status": "filled"}
    merged["algo"] = algo
    merged["algo_meta"] = meta
    merged["slices_exec"] = out
    return merged

def place_ai_driven_hyperliquid_order(
    account_ids: Optional[Iterable[int]] = None,
    account_id: Optional[int] = None,
    bypass_auto_trading: bool = False,
    trigger_context: Optional[Dict[str, Any]] = None,
) -> None:
    """Place Hyperliquid perpetual contract order based on AI decision.

    This function handles real trading on Hyperliquid exchange, supporting:
    - Perpetual contract trading (long/short)
    - Leverage (1x-50x based on account configuration)
    - Environment isolation (testnet/mainnet)
    - Position management

    Args:
        account_ids: Optional iterable of account IDs to process
        account_id: Optional single account ID to process
        trigger_context: Optional context about what triggered this decision (signal or scheduled)
    """

    try:
        from services.hyperliquid_environment import get_hyperliquid_client
        from backend.database.models import HyperliquidPosition
    except Exception as e:
        logger.error(f"Error in place_ai_driven_hyperliquid_order start: {e}", exc_info=True)
        return

    # First, get accounts list with minimal database connection
    accounts = []
    db = SessionLocal()
    # PostgreSQL handles concurrent access natively
    try:
        # Handle single account strategy trigger (manual trigger)
        if account_id is not None:
            account = db.query(Account).filter(Account.id == account_id).first()
            if not account or account.is_active != "true":
                logger.debug(f"Account {account_id} not found or inactive")
                return

            if not bypass_auto_trading and getattr(account, "auto_trading_enabled", "false") != "true":
                logger.debug(
                    "Account %s auto trading disabled - skipping Hyperliquid AI order",
                    account_id,
                )
                return

            accounts = [account]
        else:
            # Get all active accounts with auto trading enabled AND Hyperliquid enabled
            accounts = db.query(Account).filter(
                Account.is_active == "true",
                Account.auto_trading_enabled == "true",
                Account.hyperliquid_enabled == "true"  # 只处理启用了Hyperliquid的账户
            ).all()

            if not accounts:
                logger.debug("No active accounts with auto trading enabled")
                return

            if account_ids is not None:
                id_set = {int(acc_id) for acc_id in account_ids}
                accounts = [acc for acc in accounts if acc.id in id_set]
                if not accounts:
                    logger.debug(f"No matching Hyperliquid accounts for provided IDs: {account_ids}")
                    return
    finally:
        db.close()

    # Determine configured Hyperliquid symbols
    selected_symbols = get_hyperliquid_selected_symbols()
    if not selected_symbols:
        logger.warning("No Hyperliquid watchlist configured, skipping Hyperliquid trading")
        return

    prices = _get_market_prices(selected_symbols)
    if not prices:
        logger.warning("Failed to fetch market prices, skipping Hyperliquid trading")
        return

    # Sampling data availability (informational)
    from services.sampling_pool import sampling_pool
    available_symbols = []
    for sym in selected_symbols:
        samples_data = sampling_pool.get_samples(sym)
        if samples_data:
            available_symbols.append(sym)

    if available_symbols:
        logger.info(f"Available sampling symbols for Hyperliquid: {', '.join(available_symbols)}")
    else:
        logger.warning("暂无已配置Hyperliquid交易对的采样数据")

    symbol_metadata_map = get_hyperliquid_symbol_map()
    prompt_symbol_metadata = {}
    for sym in selected_symbols:
        entry = dict(symbol_metadata_map.get(sym, {}))
        entry.setdefault("name", sym)
        prompt_symbol_metadata[sym] = entry
    symbol_whitelist = set(selected_symbols)

    # Process each account with separate database connections
    for account in accounts:
        # Each account gets its own database connection
        db = SessionLocal()
        # PostgreSQL handles concurrent access natively
        try:
            # Resolve LLM config from library BEFORE validation
            resolve_account_llm_config(db, account)

            # Validate account configuration completeness
            validation_errors = []

            if not account.api_key or not account.model:
                validation_errors.append("AI model/API key not configured (check LLM Config Library in Settings)")

            # Check strategy configuration
            from backend.database.models import AccountStrategyConfig
            strategy = db.query(AccountStrategyConfig).filter(
                AccountStrategyConfig.account_id == account.id,
                AccountStrategyConfig.enabled == "true"
            ).first()
            if not strategy:
                validation_errors.append("trading strategy not configured or disabled")

            # If there are validation errors, skip this account with clear warning
            if validation_errors:
                logger.warning(
                    f"⚠️  AI交易员 '{account.name}' (ID: {account.id}) 已跳过 - "
                    f"配置不完整: {', '.join(validation_errors)}. "
                    f"请在AI交易员管理页面完成配置。"
                )
                continue

            # Get global trading mode (environment) for Hyperliquid
            from services.hyperliquid_environment import get_global_trading_mode, get_leverage_settings
            environment = get_global_trading_mode(db)
            logger.info(f"处理Hyperliquid交易账户: {account.name} (环境: {environment})")

            # Get Hyperliquid client (will check wallet configuration)
            try:
                client = get_hyperliquid_client(db, account.id, override_environment=environment)
            except ValueError as wallet_err:
                # Wallet not configured - 仅首次/变更时告警，之后只 debug
                _fn = place_ai_driven_hyperliquid_order
                if not hasattr(_fn, "_wallet_warn_seen"):
                    _fn._wallet_warn_seen = set()
                _warn_key = (account.id, str(wallet_err))
                if _warn_key not in _fn._wallet_warn_seen:
                    logger.warning(
                        f"⚠️  AI交易员 '{account.name}' (ID: {account.id}) 已跳过 - "
                        f"Hyperliquid钱包未配置。{str(wallet_err)} "
                        f"请在AI交易员管理页面配置钱包。（后续同错误仅 debug 级输出）"
                    )
                    _fn._wallet_warn_seen.add(_warn_key)
                else:
                    logger.debug(
                        f"[HL] {account.name} 钱包未配置，已跳过（已告警过一次）")
                continue
            except Exception as client_err:
                logger.error(f"获取Hyperliquid客户端失败 {account.name}: {client_err}")
                continue
            wallet_address = getattr(client, "wallet_address", None)
            decision_kwargs = {"wallet_address": wallet_address}

            # Get tracking fields for decision analysis (failures should not affect core business)
            try:
                from backend.database.models import AccountPromptBinding
                binding = db.query(AccountPromptBinding).filter_by(account_id=account.id).first()
                decision_kwargs["prompt_template_id"] = binding.prompt_template_id if binding else None
            except Exception as e:
                logger.warning(f"Failed to get prompt_template_id for {account.name}: {e}")
                decision_kwargs["prompt_template_id"] = None

            # Get signal_trigger_id from trigger_context (only present for signal-triggered decisions)
            decision_kwargs["signal_trigger_id"] = (
                trigger_context.get("signal_trigger_id") if trigger_context else None
            )

            # Get real account state from Hyperliquid
            try:
                account_state = client.get_account_state(db)
                available_balance = account_state['available_balance']
                total_equity = account_state['total_equity']
                margin_usage = account_state['margin_usage_percent']

                logger.info(
                    f"Hyperliquid account state for {account.name}: "
                    f"equity=${total_equity:.2f}, available=${available_balance:.2f}, "
                    f"margin_usage={margin_usage:.1f}%"
                )

            except Exception as state_err:
                logger.error(f"Failed to get account state for {account.name}: {state_err}")
                continue

            # Get open positions from Hyperliquid (must check before skipping due to equity)
            # include_timing=True to get position opened times for AI prompt context
            try:
                positions = client.get_positions(db, include_timing=True)
                logger.info(f"Account {account.name} has {len(positions)} open positions")
            except Exception as pos_err:
                logger.error(f"Failed to get positions for {account.name}: {pos_err}")
                positions = []

            # Check available balance for trading - minimum 10 USDT required
            MIN_AVAILABLE_BALANCE = 10  # Minimum 10 USDT to open new positions
            
            # Check equity after getting positions - allow close operations even with zero equity
            if total_equity <= 0 and len(positions) == 0:
                logger.warning(
                    f"⚠️  Account {account.name} (ID: {account.id}) skipped - No balance to trade! "
                    f"Equity: ${total_equity:.2f}, Positions: 0. "
                    f"Please deposit funds to wallet {wallet_address} to enable trading."
                )
                continue

            if total_equity <= 0 and len(positions) > 0:
                logger.warning(
                    f"⚠️  Account {account.name} (ID: {account.id}) has ZERO equity but {len(positions)} open positions! "
                    f"Equity: ${total_equity:.2f}, Allowing AI to decide on close/risk management operations."
                )
            
            # Check minimum available balance for opening new positions
            can_open_new_positions = available_balance >= MIN_AVAILABLE_BALANCE
            if not can_open_new_positions and len(positions) == 0:
                logger.warning(
                    f"⚠️  Account {account.name} (ID: {account.id}) skipped - Available balance ${available_balance:.2f} < ${MIN_AVAILABLE_BALANCE} minimum. "
                    f"Please deposit more funds to wallet {wallet_address} to enable trading."
                )
                continue
            
            if not can_open_new_positions:
                logger.info(
                    f"Account {account.name} available balance ${available_balance:.2f} < ${MIN_AVAILABLE_BALANCE}, "
                    f"only close/hold operations allowed"
                )

            # Build portfolio data for AI (using Hyperliquid real data)
            portfolio = {
                'cash': available_balance,
                'frozen_cash': account_state.get('used_margin', 0),
                'positions': {},
                'total_assets': total_equity
            }

            for pos in positions:
                symbol = pos['coin']
                portfolio['positions'][symbol] = {
                    'quantity': pos['szi'],  # Signed size
                    'avg_cost': pos['entry_px'],
                    'current_value': pos['position_value'],
                    'unrealized_pnl': pos['unrealized_pnl'],
                    'leverage': pos['leverage']
                }

            # Build Hyperliquid state for prompt context
            hyperliquid_state = {
                'total_equity': total_equity,
                'available_balance': available_balance,
                'used_margin': account_state.get('used_margin', 0),
                'margin_usage_percent': margin_usage,
                'maintenance_margin': account_state.get('maintenance_margin', 0),
                'positions': positions
            }

            # ========================================
            # RISK CONTROL CHECK - Account Level
            # Check daily loss circuit breaker before AI decision
            # ========================================
            risk_service = get_risk_control_service()
            risk_service.load_config_from_db(db, account.id)
            
            # Check daily loss breaker (account level check)
            daily_loss_check = risk_service.check_daily_loss_breaker(db, account.id, total_equity)
            if daily_loss_check.result == RiskCheckResult.BLOCKED:
                logger.warning(
                    f"[RISK] Account {account.name} blocked by circuit breaker: {daily_loss_check.message}"
                )
                # Log this as a system event, skip AI decision entirely
                continue
            elif daily_loss_check.result == RiskCheckResult.WARNING:
                logger.info(f"[RISK] Account {account.name} warning: {daily_loss_check.message}")

            # FullAuto 等上游可能已在 trigger_context 里提供了决策
            pre_made = (trigger_context.get("pre_made_decisions") if trigger_context else None)
            if pre_made:
                decisions = pre_made
                logger.info(f"[HYPERLIQUID] Using {len(decisions)} pre-made decisions for {account.name}")
            else:
                decisions = call_ai_for_decision(
                    db,
                    account,
                    portfolio,
                    prices,
                    symbols=selected_symbols,
                    hyperliquid_state=hyperliquid_state,
                    symbol_metadata=prompt_symbol_metadata,
                    trigger_context=trigger_context,
                )

            if not decisions:
                logger.warning(f"Failed to get AI decision for {account.name}, skipping")
                continue

            decision_priority = {"close": 0, "sell": 1, "buy": 2, "hold": 3}
            ordered_decisions = sorted(
                decisions,
                key=lambda d: decision_priority.get(str(d.get("operation", "")).lower(), 4),
            )

            for decision in ordered_decisions:
                if not isinstance(decision, dict):
                    logger.warning(f"Skipping malformed Hyperliquid decision for {account.name}: {decision}")
                    continue

                operation = decision.get("operation", "").lower()
                symbol = decision.get("symbol", "").upper()
                target_portion = float(decision.get("target_portion_of_balance", 0))
                leverage = int(decision.get("leverage", getattr(account, "default_leverage", 10)))
                # 同币已有仓：adopt 交易所杠杆（一仓一杠杆）
                try:
                    from backend.services.leverage_authority import extract_existing_symbol_leverage
                    _adopt_lev = extract_existing_symbol_leverage(symbol, positions)
                    if _adopt_lev is not None:
                        leverage = max(1, int(round(float(_adopt_lev))))
                        decision["leverage"] = leverage
                except Exception:
                    pass
                max_price = decision.get("max_price")
                min_price = decision.get("min_price")
                reason = decision.get("reason", "No reason provided")

                # ── Phase 3B §修复①: 服务层硬性验证 ──
                # 0. Validate and auto-fill AI decision fields
                decision, autofill_warnings = _validate_and_autofill_decision(
                    decision, prices, symbol_whitelist,
                )
                for w in autofill_warnings:
                    logger.warning(f"[AI AutoFill] {w}")
                # Re-read potentially updated fields
                operation = decision.get("operation", "").lower()
                symbol = decision.get("symbol", "").upper()
                max_price = decision.get("max_price")
                min_price = decision.get("min_price")

                # 0.5. 流动性检查（Phase 3-5：LiquidityFilter）
                if operation in ("buy", "sell"):
                    try:
                        from backend.services.liquidity_filter import liquidity_filter
                        liq_passed, liq_result = liquidity_filter.check(
                            symbol=symbol,
                            order_size_usd=target_portion * float(
                                (hyperliquid_state or {}).get("total_equity", 0) or 10000
                            ),
                        )
                        if not liq_passed:
                            logger.warning(
                                f"[LiquidityFilter] 流动性不足，跳过 {operation} {symbol}: {liq_result.reason}"
                            )
                            save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                            try:
                                from backend.services.unified_risk_gate import record_guard_block
                                record_guard_block(
                                    db, account_id=account.id,
                                    guard_name="liquidity_filter",
                                    symbol=symbol, side=operation,
                                    reason=liq_result.reason,
                                    extra={
                                        "volume_24h_usd": liq_result.volume_24h_usd,
                                        "order_size_usd": liq_result.order_size_usd,
                                        "depth_usd": liq_result.depth_usd,
                                        "impact_pct": liq_result.impact_pct,
                                    },
                                )
                            except Exception:
                                pass
                            continue
                    except Exception as e:
                        logger.warning(f"[LiquidityFilter] 流动性检查异常（跳过），symbol={symbol}: {e}")

                # 1. 置信度门槛：< 60% 自动降级为 HOLD (using normalized confidence)
                raw_conf = decision.get("confidence", 1.0)
                confidence = _normalize_confidence(raw_conf)
                if confidence < 0.6 and operation in ("buy", "sell"):
                    logger.warning(
                        f"[RiskGuard] 置信度 {confidence:.2f} < 0.6，决策降级为 HOLD: {symbol}"
                    )
                    decision["operation"] = "hold"
                    operation = "hold"

                # 2. 强制仓位上限 20%
                if target_portion > 0.20:
                    logger.warning(
                        f"[RiskGuard] AI 请求仓位 {target_portion:.0%} 超过硬性上限20%，截断至20%"
                    )
                    target_portion = 0.20
                    decision["target_portion_of_balance"] = 0.20

                # 3. [2026-09-07 杠杆根治 P1] 杠杆 = 币种档（leverage_authority 唯一权威），
                # AI 自报杠杆一律忽略；SYMBOL_LEVERAGE_ENABLED=false 时回退旧 5-20x 钳制。
                try:
                    from backend.services.leverage_authority import (
                        symbol_leverage_enabled as _sym_lev_on,
                        resolve_leverage as _auth_lev_rg,
                    )
                    if _sym_lev_on():
                        leverage = _auth_lev_rg(tier=None, symbol=symbol)
                    else:
                        leverage = max(5, min(20, leverage))
                except Exception:
                    leverage = max(5, min(20, leverage))

                # 4. buy/sell 必须有止损价 (auto-filled above, but double-check)
                if operation in ("buy", "sell") and not decision.get("stop_loss_price"):
                    logger.warning(
                        f"[RiskGuard] 缺少止损价格（自动补充失败），拒绝执行 {operation} {symbol}"
                    )
                    save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                    continue

                logger.info(
                    f"AI decision for {account.name}: {operation} {symbol} "
                    f"(portion: {target_portion:.2%}, leverage: {leverage}x, max_price: {max_price}, min_price: {min_price}) - {reason}"
                )

                if operation not in ["buy", "sell", "hold", "close"]:
                    logger.warning(f"Invalid operation '{operation}' from AI for {account.name}")
                    save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                    continue

                # 统一管道: 通过 position_memory_manager 评估交易质量
                if operation in ("buy", "sell"):
                    try:
                        from backend.services.position_memory_manager import position_manager
                        _mem_regime = "unknown"
                        _mem_price = prices.get(symbol, 0) or 0
                        _plan = position_manager.evaluate_trade(
                            db=db,
                            account_id=account.id,
                            symbol=symbol,
                            side=operation,
                            ai_confidence=confidence,
                            current_price=_mem_price,
                            signal_source="ai_live",
                            market_regime=_mem_regime,
                            volatility_pct=0.015,
                            raw_leverage=leverage,
                            raw_tp_price=float(decision.get("take_profit_price", 0) or 0),
                            raw_sl_price=float(decision.get("stop_loss_price", 0) or 0),
                        )
                        if _plan.action == "skip":
                            logger.info(
                                f"[PosMgr] Live交易被仓位管理器跳过: "
                                f"{symbol} {operation} — {_plan.reasoning}")
                            save_ai_decision(
                                db, account, decision, portfolio, executed=False,
                                **decision_kwargs)
                            continue
                        # 使用管理器输出的杠杆和仓位比例（如果可用）
                        if _plan.leverage and _plan.leverage > 0:
                            leverage = _plan.leverage
                        if _plan.size_pct and _plan.size_pct > 0:
                            target_portion = min(target_portion, _plan.size_pct)
                            decision["target_portion_of_balance"] = target_portion
                    except Exception as _pm_err:
                        logger.debug(
                            f"[PosMgr] Live仓位评估异常(非阻断): {_pm_err}")

                if operation == "hold":
                    # HOLD = do nothing. Ignore all other fields (leverage, TP/SL, etc.)
                    # AI may include extra fields due to compliance behavior, but we enforce
                    # the rule: TP/SL can only be set at entry, not modified during hold.
                    logger.info(f"AI decided to HOLD for {account.name} - no action taken")
                    save_ai_decision(db, account, decision, portfolio, executed=True, **decision_kwargs)
                    continue

                if symbol not in symbol_whitelist:
                    logger.warning(f"Symbol '{symbol}' not in Hyperliquid watchlist for {account.name}")
                    save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                    continue

                # Get leverage settings from HyperliquidWallet (or Account fallback)
                leverage_settings = get_leverage_settings(db, account.id, environment)
                max_leverage = leverage_settings["max_leverage"]
                default_leverage = leverage_settings["default_leverage"]

                # Read global margin mode from SystemConfig
                _is_cross_margin = False
                try:
                    margin_cfg = db.query(SystemConfig).filter(
                        SystemConfig.key == "global_margin_mode"
                    ).first()
                    if margin_cfg and margin_cfg.value == "cross":
                        _is_cross_margin = True
                except Exception:
                    pass

                if leverage < 1 or leverage > max_leverage:
                    logger.warning(
                        f"Invalid leverage {leverage}x from AI (max: {max_leverage}x), "
                        f"using default {default_leverage}x"
                    )
                    leverage = default_leverage

                if target_portion <= 0 or target_portion > 1:
                    logger.warning(f"Invalid target_portion {target_portion} from AI for {account.name}")
                    save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                    continue

                price = prices.get(symbol)
                if not price or price <= 0:
                    logger.warning(f"Invalid price for {symbol} for {account.name}")
                    save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                    continue

                order_result = None

                if operation == "buy":
                    # Check minimum available balance before opening new position
                    if available_balance < MIN_AVAILABLE_BALANCE:
                        logger.warning(
                            f"Available balance ${available_balance:.2f} < ${MIN_AVAILABLE_BALANCE} minimum, "
                            f"skipping BUY operation for {symbol}"
                        )
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue
                    
                    # Calculate margin first, then position value with leverage
                    margin = available_balance * target_portion
                    order_value = margin * leverage
                    quantity = round(order_value / price, 6)

                    # [2026-09-03 v3 方向1] PositionConstruction 单一权威：AI 的 portion×leverage 只是提议
                    order_value, leverage, quantity, _pc_block = _v3_position_construction_live(
                        db, account, symbol, price=price, order_value=order_value, leverage=leverage,
                        total_equity=total_equity, decision=decision, exchange="hyperliquid",
                    )
                    if _pc_block:
                        decision["error_reason"] = _pc_block
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue
                    margin = order_value / max(1.0, float(leverage or 1.0))

                    # ========================================
                    # RISK CONTROL CHECK - Before Order Execution # 深挖第 3 轮 (2026-05-08)：UnifiedRiskGate 跑两层硬规则 + 带状态规则
                    # ========================================
                    try:
                        from backend.services.unified_risk_gate import unified_check as _uc_live
                        _existing_live = [
                            {
                                "symbol": p.get("coin", "") if isinstance(p, dict) else "",
                                "side": ("long" if (isinstance(p, dict) and float(p.get("szi", 0) or 0) > 0) else "short"),
                                "margin": float(p.get("marginUsed", 0) or 0) if isinstance(p, dict) else 0.0,
                                "notional": (abs(float(p.get("szi", 0) or 0)) * float(p.get("entryPx", 0) or 0)) if isinstance(p, dict) else 0.0,
                                "size": abs(float(p.get("szi", 0) or 0)) if isinstance(p, dict) else 0.0,
                                "leverage": float((p.get("leverage", {}) or {}).get("value", 1)) if isinstance(p, dict) and isinstance(p.get("leverage"), dict) else float((p.get("leverage") or 1) if isinstance(p, dict) else 1),
                            }
                            for p in (positions or [])
                            if isinstance(p, dict)
                        ]
                        _ures_live = _uc_live(
                            db=db, account_id=account.id,
                            symbol=symbol, side=operation,
                            notional=order_value, margin=margin, leverage=leverage,
                            total_equity=total_equity, available_balance=available_balance,
                            frozen_margin=max(0.0, total_equity - available_balance),
                            margin_usage_percent=margin_usage,
                            existing_positions=_existing_live,
                            op_source="live",
                        )
                        if not _ures_live.passed:
                            logger.warning(
                                f"[RISK][unified] Order blocked for {account.name}: "
                                f"{_ures_live.reason_text} [layer={_ures_live.blocked_layer} rule={_ures_live.blocked_rule}]"
                            )
                            # [2026-09-03 修复] save_ai_decision 无 reason 形参，原写法在拦截路径抛 TypeError
                            decision["error_reason"] = f"统一风控拦截: {_ures_live.reason_text}"
                            save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                            continue
                    except Exception as _uc_err:
                        logger.debug(f"[RISK][unified] 跳过统一风控: {_uc_err}")

                    # [2026-09-03 v3 方向7] RiskEngine 单入口（急停/状态机/连通性/避险窗口/日配额）
                    _v3_block = _v3_risk_pre_trade_live(
                        db, account, symbol, operation, order_value=order_value, total_equity=total_equity,
                        leverage=leverage, exchange="hyperliquid", decision=decision,
                    )
                    if _v3_block:
                        decision["error_reason"] = _v3_block
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue

                    risk_allowed, risk_message = check_risk_before_trade(
                        db=db,
                        account_id=account.id,
                        symbol=symbol,
                        operation=operation,
                        order_value=order_value,
                        total_equity=total_equity,
                        available_balance=available_balance,
                        positions=positions,
                        margin_usage_percent=margin_usage,
                    )
                    
                    if not risk_allowed:
                        logger.warning(
                            f"[RISK] Order blocked for {account.name}: {risk_message}"
                        )
                        # [2026-09-03 修复] save_ai_decision 无 reason 形参，原写法在拦截路径抛 TypeError
                        decision["error_reason"] = f"风控检查拒绝: {risk_message}"
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue

                    logger.info(
                        f"Position sizing for {symbol}: "
                        f"margin=${margin:.2f} ({target_portion:.1%} of ${available_balance:.2f}), "
                        f"leverage={leverage}x, position_value=${order_value:.2f}, quantity={quantity}"
                    )

                    # Extract TP/SL and time_in_force from AI decision
                    take_profit_price = decision.get("take_profit_price")
                    stop_loss_price = decision.get("stop_loss_price")
                    time_in_force = decision.get("time_in_force", "Ioc")  # Default to Ioc (market-like)

                    # Price validation for BUY operation
                    if max_price is not None:
                        price_to_use = max_price
                        price_to_use, price_deviation_percent, _ = _enforce_price_bounds(
                            symbol=symbol,
                            account_name=account.name,
                            operation="buy",
                            current_price=price,
                            requested_price=price_to_use,
                        )
                        logger.info(
                            f"Using AI-provided max_price for BUY {symbol}: "
                            f"market=${price:.2f}, order=${price_to_use:.2f}, "
                            f"deviation={price_deviation_percent:.2f}%"
                        )
                    else:
                        # AI did not provide max_price - use market price (already within 1%)
                        price_to_use = price
                        logger.warning(
                            f"⚠️  AI COMPLIANCE ISSUE - BUY {symbol}: "
                            f"AI did not provide max_price in decision. "
                            f"Using market price: ${price_to_use:.2f}. "
                            f"Prompt should require max_price for all BUY operations."
                        )

                    logger.info(
                        f"[HYPERLIQUID {environment.upper()}] Placing BUY order: "
                        f"{symbol} size={quantity} leverage={leverage}x TIF={time_in_force} "
                        f"TP={take_profit_price} SL={stop_loss_price}"
                    )

                    # Use native API for all orders (isolated margin by default)
                    order_result = _place_hl_order_with_algo(
                        client, decision,
                        db=db, symbol=symbol, is_buy=True,
                        quantity=quantity, price=price_to_use, leverage=leverage,
                        time_in_force=time_in_force,
                        take_profit_price=take_profit_price,
                        stop_loss_price=stop_loss_price, is_cross=_is_cross_margin,
                    )

                    # Fallback: If IOC failed due to no liquidity, retry with GTC at improved price
                    if order_result and order_result.get('status') == 'error':
                        error_msg = order_result.get('error', '')
                        if 'could not immediately match' in error_msg.lower() or 'no resting orders' in error_msg.lower():
                            # Use slightly more aggressive price for GTC to increase fill probability
                            gtc_price = price * 1.002  # 0.2% above market for buy
                            gtc_price, _, _ = _enforce_price_bounds(
                                symbol=symbol,
                                account_name=account.name,
                                operation="buy",
                                current_price=price,
                                requested_price=gtc_price,
                            )
                            logger.warning(
                                f"⚠️  IOC order failed for BUY {symbol} (no liquidity), "
                                f"retrying with GTC limit order at ${gtc_price:.2f}..."
                            )
                            order_result = _place_hl_order_with_algo(
                                client, decision,
                                db=db, symbol=symbol, is_buy=True,
                                quantity=quantity, price=gtc_price, leverage=leverage,
                                time_in_force="Gtc",
                                take_profit_price=take_profit_price,
                                stop_loss_price=stop_loss_price, is_cross=_is_cross_margin,
                            )
                            if order_result and order_result.get('status') in ['filled', 'resting']:
                                logger.info(f"GTC fallback order succeeded for BUY {symbol} at ${gtc_price:.2f}")

                elif operation == "sell":
                    # Check minimum available balance before opening new position
                    if available_balance < MIN_AVAILABLE_BALANCE:
                        logger.warning(
                            f"Available balance ${available_balance:.2f} < ${MIN_AVAILABLE_BALANCE} minimum, "
                            f"skipping SELL (short) operation for {symbol}"
                        )
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue
                    
                    # Calculate margin first, then position value with leverage
                    margin = available_balance * target_portion
                    order_value = margin * leverage
                    quantity = round(order_value / price, 6)

                    # [2026-09-03 v3 方向1] PositionConstruction 单一权威：AI 的 portion×leverage 只是提议
                    order_value, leverage, quantity, _pc_block = _v3_position_construction_live(
                        db, account, symbol, price=price, order_value=order_value, leverage=leverage,
                        total_equity=total_equity, decision=decision, exchange="hyperliquid",
                    )
                    if _pc_block:
                        decision["error_reason"] = _pc_block
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue
                    margin = order_value / max(1.0, float(leverage or 1.0))

                    # ========================================
                    # RISK CONTROL CHECK - Before Order Execution # 深挖第 3 轮 (2026-05-08)：UnifiedRiskGate 跑两层硬规则 + 带状态规则
                    # ========================================
                    try:
                        from backend.services.unified_risk_gate import unified_check as _uc_live
                        _existing_live = [
                            {
                                "symbol": p.get("coin", "") if isinstance(p, dict) else "",
                                "side": ("long" if (isinstance(p, dict) and float(p.get("szi", 0) or 0) > 0) else "short"),
                                "margin": float(p.get("marginUsed", 0) or 0) if isinstance(p, dict) else 0.0,
                                "notional": (abs(float(p.get("szi", 0) or 0)) * float(p.get("entryPx", 0) or 0)) if isinstance(p, dict) else 0.0,
                                "size": abs(float(p.get("szi", 0) or 0)) if isinstance(p, dict) else 0.0,
                                "leverage": float((p.get("leverage", {}) or {}).get("value", 1)) if isinstance(p, dict) and isinstance(p.get("leverage"), dict) else float((p.get("leverage") or 1) if isinstance(p, dict) else 1),
                            }
                            for p in (positions or [])
                            if isinstance(p, dict)
                        ]
                        _ures_live = _uc_live(
                            db=db, account_id=account.id,
                            symbol=symbol, side=operation,
                            notional=order_value, margin=margin, leverage=leverage,
                            total_equity=total_equity, available_balance=available_balance,
                            frozen_margin=max(0.0, total_equity - available_balance),
                            margin_usage_percent=margin_usage,
                            existing_positions=_existing_live,
                            op_source="live",
                        )
                        if not _ures_live.passed:
                            logger.warning(
                                f"[RISK][unified] Order blocked for {account.name}: "
                                f"{_ures_live.reason_text} [layer={_ures_live.blocked_layer} rule={_ures_live.blocked_rule}]"
                            )
                            # [2026-09-03 修复] save_ai_decision 无 reason 形参，原写法在拦截路径抛 TypeError
                            decision["error_reason"] = f"统一风控拦截: {_ures_live.reason_text}"
                            save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                            continue
                    except Exception as _uc_err:
                        logger.debug(f"[RISK][unified] 跳过统一风控: {_uc_err}")

                    # [2026-09-03 v3 方向7] RiskEngine 单入口（急停/状态机/连通性/避险窗口/日配额）
                    _v3_block = _v3_risk_pre_trade_live(
                        db, account, symbol, operation, order_value=order_value, total_equity=total_equity,
                        leverage=leverage, exchange="hyperliquid", decision=decision,
                    )
                    if _v3_block:
                        decision["error_reason"] = _v3_block
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue

                    risk_allowed, risk_message = check_risk_before_trade(
                        db=db,
                        account_id=account.id,
                        symbol=symbol,
                        operation=operation,
                        order_value=order_value,
                        total_equity=total_equity,
                        available_balance=available_balance,
                        positions=positions,
                        margin_usage_percent=margin_usage,
                    )
                    
                    if not risk_allowed:
                        logger.warning(
                            f"[RISK] Order blocked for {account.name}: {risk_message}"
                        )
                        # [2026-09-03 修复] save_ai_decision 无 reason 形参，原写法在拦截路径抛 TypeError
                        decision["error_reason"] = f"风控检查拒绝: {risk_message}"
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue

                    logger.info(
                        f"Position sizing for {symbol}: "
                        f"margin=${margin:.2f} ({target_portion:.1%} of ${available_balance:.2f}), "
                        f"leverage={leverage}x, position_value=${order_value:.2f}, quantity={quantity}"
                    )

                    # Extract TP/SL and time_in_force from AI decision
                    take_profit_price = decision.get("take_profit_price")
                    stop_loss_price = decision.get("stop_loss_price")
                    time_in_force = decision.get("time_in_force", "Ioc")  # Default to Ioc (market-like)

                    # Price validation for SELL operation
                    if min_price is not None:
                        price_to_use = min_price
                        price_to_use, price_deviation_percent, _ = _enforce_price_bounds(
                            symbol=symbol,
                            account_name=account.name,
                            operation="sell",
                            current_price=price,
                            requested_price=price_to_use,
                        )
                        logger.info(
                            f"Using AI-provided min_price for SELL {symbol}: "
                            f"market=${price:.2f}, order=${price_to_use:.2f}, "
                            f"deviation={price_deviation_percent:.2f}%"
                        )
                    else:
                        # AI did not provide min_price - use market price
                        price_to_use = price
                        logger.warning(
                            f"⚠️  AI COMPLIANCE ISSUE - SELL {symbol}: "
                            f"AI did not provide min_price in decision. "
                            f"Using market price: ${price_to_use:.2f}. "
                            f"Prompt should require min_price for all SELL operations."
                        )

                    logger.info(
                        f"[HYPERLIQUID {environment.upper()}] Placing SELL order: "
                        f"{symbol} size={quantity} leverage={leverage}x TIF={time_in_force} "
                        f"TP={take_profit_price} SL={stop_loss_price}"
                    )

                    # Use native API for all orders (isolated margin by default)
                    order_result = _place_hl_order_with_algo(
                        client, decision,
                        db=db, symbol=symbol, is_buy=False,
                        quantity=quantity, price=price_to_use, leverage=leverage,
                        time_in_force=time_in_force,
                        take_profit_price=take_profit_price,
                        stop_loss_price=stop_loss_price, is_cross=_is_cross_margin,
                    )

                    # Fallback: If IOC failed due to no liquidity, retry with GTC at improved price
                    if order_result and order_result.get('status') == 'error':
                        error_msg = order_result.get('error', '')
                        if 'could not immediately match' in error_msg.lower() or 'no resting orders' in error_msg.lower():
                            # Use slightly more aggressive price for GTC to increase fill probability
                            gtc_price = price * 0.998  # 0.2% below market for sell
                            gtc_price, _, _ = _enforce_price_bounds(
                                symbol=symbol,
                                account_name=account.name,
                                operation="sell",
                                current_price=price,
                                requested_price=gtc_price,
                            )
                            logger.warning(
                                f"⚠️  IOC order failed for SELL {symbol} (no liquidity), "
                                f"retrying with GTC limit order at ${gtc_price:.2f}..."
                            )
                            order_result = _place_hl_order_with_algo(
                                client, decision,
                                db=db, symbol=symbol, is_buy=False,
                                quantity=quantity, price=gtc_price, leverage=leverage,
                                time_in_force="Gtc",
                                take_profit_price=take_profit_price,
                                stop_loss_price=stop_loss_price, is_cross=_is_cross_margin,
                            )
                            if order_result and order_result.get('status') in ['filled', 'resting']:
                                logger.info(f"GTC fallback order succeeded for SELL {symbol} at ${gtc_price:.2f}")

                elif operation == "close":
                    # Close cooldown check - prevent over-closing
                    can_close, remaining = _close_cooldown_manager.can_close(account.id, symbol)
                    if not can_close:
                        logger.warning(
                            f"[CLOSE COOLDOWN] {symbol} in cooldown for {remaining:.0f}s, "
                            f"skipping close for {account.name}"
                        )
                        decision["error_reason"] = f"平仓冷却期中({remaining:.0f}s)"
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue

                    # For close operations, only check if account is under circuit breaker
                    # Allow closing positions even if other risk limits are reached
                    risk_service = get_risk_control_service()
                    risk_service.load_config_from_db(db, account.id)
                    
                    daily_loss_check = risk_service.check_daily_loss_breaker(db, account.id, total_equity)
                    if daily_loss_check.result == RiskCheckResult.BLOCKED:
                        logger.warning(
                            f"[RISK] Close operation blocked for {account.name} due to circuit breaker: {daily_loss_check.message}"
                        )
                        decision["error_reason"] = f"熔断状态下禁止操作: {daily_loss_check.message}"
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue
                    
                    position_to_close = None
                    for pos in positions:
                        if pos.get('coin') == symbol:
                            position_to_close = pos
                            break

                    if position_to_close:
                        position_size = abs(position_to_close.get('szi', 0))
                        is_long = (position_to_close.get('szi', 0) or 0) > 0
                    else:
                        # Fall back to portfolio snapshot from prompt context
                        logger.warning(
                            f"⚠️  Position {symbol} not found in real-time positions list. "
                            f"Using portfolio snapshot data from AI prompt context. "
                            f"This may indicate position was closed by another operation or data sync issue."
                        )
                        portfolio_positions = portfolio.get('positions') or {}
                        fallback_position = portfolio_positions.get(symbol)
                        if not fallback_position:
                            logger.warning(f"Unable to locate Hyperliquid position data for {symbol}; skipping close.")
                            save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                            continue
                        quantity = float(fallback_position.get('quantity') or 0)
                        position_size = abs(quantity)
                        is_long = quantity > 0

                    # Validate position exists and size is non-zero
                    if position_size <= 0:
                        logger.warning(f"No position to close for {symbol} (size={position_size}), skipping close operation")
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                        continue

                    close_size = position_size * target_portion

                    logger.info(
                        f"[HYPERLIQUID {environment.upper()}] Closing position: "
                        f"{symbol} size={close_size} (closing {'long' if is_long else 'short'})"
                    )

                    current_price = prices.get(symbol, 0)

                    # Price validation for Hyperliquid 1% oracle limit
                    max_price_close = decision.get("max_price")

                    if is_long:
                        ai_close_price = min_price
                        price_field_used = "min_price"
                    else:
                        ai_close_price = max_price_close if max_price_close is not None else min_price
                        price_field_used = "max_price" if max_price_close is not None else "min_price"

                        if max_price_close is None and min_price is not None:
                            logger.warning(
                                f"⚠️  AI COMPLIANCE ISSUE - CLOSE {symbol}: "
                                f"Short position provided min_price instead of max_price. "
                                f"Treating min_price=${min_price:.2f} as max_price for compatibility."
                            )

                    if ai_close_price:
                        close_price = ai_close_price
                        close_price, price_deviation_percent, _ = _enforce_price_bounds(
                            symbol=symbol,
                            account_name=account.name,
                            operation="close",
                            current_price=current_price,
                            requested_price=close_price,
                        )

                        # Check if close price is on the wrong side of market OR too close to oracle boundaries
                        if not is_long and close_price < current_price:
                            # Close Short: buy price too low, raise to slightly above market
                            logger.warning(
                                f"⚠️  AI COMPLIANCE ISSUE - CLOSE {symbol}: "
                                f"Short close limit ${close_price:.2f} sits below market ${current_price:.2f}. "
                                f"Adjusting to ensure IOC buy can match resting asks."
                            )
                            close_price, price_deviation_percent, _ = _enforce_price_bounds(
                                symbol=symbol,
                                account_name=account.name,
                                operation="close",
                                current_price=current_price,
                                requested_price=current_price * 1.005,
                            )
                        elif not is_long and close_price > current_price * 1.005:
                            # Close Short: buy price too close to +1% oracle limit
                            logger.warning(
                                f"⚠️  AI COMPLIANCE ISSUE - CLOSE {symbol}: "
                                f"Short close limit ${close_price:.2f} too close to oracle upper boundary. "
                                f"Market ${current_price:.2f}. Adjusting to 1.005x for safer execution."
                            )
                            close_price, price_deviation_percent, _ = _enforce_price_bounds(
                                symbol=symbol,
                                account_name=account.name,
                                operation="close",
                                current_price=current_price,
                                requested_price=current_price * 1.005,
                            )
                        elif is_long and close_price > current_price:
                            # Close Long: sell price too high, lower to slightly below market
                            logger.warning(
                                f"⚠️  AI COMPLIANCE ISSUE - CLOSE {symbol}: "
                                f"Long close limit ${close_price:.2f} sits above market ${current_price:.2f}. "
                                f"Adjusting to ensure IOC sell can match resting bids."
                            )
                            close_price, price_deviation_percent, _ = _enforce_price_bounds(
                                symbol=symbol,
                                account_name=account.name,
                                operation="close",
                                current_price=current_price,
                                requested_price=current_price * 0.995,
                            )
                        elif is_long and close_price < current_price * 0.995:
                            # Close Long: sell price too close to -1% oracle limit
                            logger.warning(
                                f"⚠️  AI COMPLIANCE ISSUE - CLOSE {symbol}: "
                                f"Long close limit ${close_price:.2f} too close to oracle lower boundary. "
                                f"Market ${current_price:.2f}. Adjusting to 0.995x for safer execution."
                            )
                            close_price, price_deviation_percent, _ = _enforce_price_bounds(
                                symbol=symbol,
                                account_name=account.name,
                                operation="close",
                                current_price=current_price,
                                requested_price=current_price * 0.995,
                            )

                        logger.info(
                            f"Using AI-provided {price_field_used} for CLOSE {symbol}: "
                            f"market=${current_price:.2f}, order=${close_price:.2f}, "
                            f"deviation={price_deviation_percent:.2f}%"
                        )
                    else:
                        # AI did not provide relevant close price - use safe default
                        fallback_multiplier = 0.995 if is_long else 1.005
                        close_price = current_price * fallback_multiplier
                        close_price, _, _ = _enforce_price_bounds(
                            symbol=symbol,
                            account_name=account.name,
                            operation="close",
                            current_price=current_price,
                            requested_price=close_price,
                        )
                        logger.warning(
                            f"⚠️  AI COMPLIANCE ISSUE - CLOSE {symbol}: "
                            f"AI did not provide {'min_price' if is_long else 'max_price'} in decision. "
                            f"Using fallback price: market=${current_price:.2f}, order=${close_price:.2f}. "
                            f"Prompt should require {'min_price for closing longs' if is_long else 'max_price for closing shorts'} in all CLOSE operations."
                        )

                    # Retry logic with progressive price adjustment for IoC close orders
                    max_retries = 4
                    retry_count = 0
                    order_result = None
                    fallback_gtc_attempted = False

                    # Progressive price multipliers for each retry (conservative start + dense sampling)
                    # For long close (sell): move down to increase match probability
                    # For short close (buy): move up to increase match probability
                    # Strategy: Start conservatively, progressively sample through safe zone to boundary
                    if is_long:
                        price_multipliers = [0.996, 0.994, 0.992, 0.99]  # Selling: 0.6% coverage, 4 sampling points
                    else:
                        price_multipliers = [1.004, 1.006, 1.008, 1.01]  # Buying: 0.6% coverage, 4 sampling points

                    while retry_count < max_retries and order_result is None:
                        # Use AI price for first attempt, then use progressive multipliers
                        if retry_count == 0:
                            attempt_price = close_price
                        else:
                            # Refresh market price for retry attempts
                            current_price_retry = prices.get(symbol, current_price)
                            attempt_price = current_price_retry * price_multipliers[retry_count]
                            attempt_price, _, _ = _enforce_price_bounds(
                                symbol=symbol,
                                account_name=account.name,
                                operation="close",
                                current_price=current_price_retry,
                                requested_price=attempt_price,
                            )
                            logger.info(
                                f"[RETRY {retry_count}/{max_retries}] CLOSE {symbol}: "
                                f"Adjusting price to ${attempt_price:.2f} "
                                f"(market=${current_price_retry:.2f}, multiplier={price_multipliers[retry_count]})"
                            )

                        # Attempt order placement (isolated margin)
                        attempt_result = client.place_order_with_tpsl(
                            db=db,
                            symbol=symbol,
                            is_buy=(not is_long),
                            size=close_size,
                            price=attempt_price,
                            leverage=1,
                            time_in_force="Ioc",
                            reduce_only=True,
                            take_profit_price=None,
                            stop_loss_price=None,
                            is_cross=_is_cross_margin,
                        )

                        # Check if order succeeded
                        if attempt_result and attempt_result.get('status') == 'filled':
                            order_result = attempt_result
                            if retry_count > 0:
                                logger.info(
                                    f"✅ CLOSE {symbol} succeeded on retry {retry_count} "
                                    f"with price ${attempt_price:.2f}"
                                )
                            break

                        # Check if we should retry
                        error_msg = attempt_result.get('error', '') if attempt_result else ''
                        should_retry = (
                            'could not immediately match' in error_msg.lower() or
                            'no resting orders' in error_msg.lower()
                        )

                        if should_retry and retry_count < max_retries - 1:
                            retry_count += 1
                            logger.warning(
                                f"⚠️  CLOSE {symbol} failed (attempt {retry_count}/{max_retries}): {error_msg}. "
                                f"Will retry with more aggressive price..."
                            )
                        else:
                            # Either non-retryable error or max retries reached
                            order_result = attempt_result
                            if retry_count > 0:
                                logger.error(
                                    f"❌ CLOSE {symbol} failed after {retry_count + 1} attempts. "
                                    f"Last error: {error_msg}"
                                )
                            break

                    # If IOC retries failed, place a final reduce-only GTC order at the safe boundary
                    if (not order_result or order_result.get('status') != 'filled') and not fallback_gtc_attempted:
                        fallback_gtc_attempted = True
                        boundary_multiplier = 0.99 if is_long else 1.01
                        latest_price = prices.get(symbol, current_price)
                        if not latest_price or latest_price <= 0:
                            latest_price = current_price or close_price
                        fallback_price = latest_price * boundary_multiplier
                        fallback_price, _, _ = _enforce_price_bounds(
                            symbol=symbol,
                            account_name=account.name,
                            operation="close",
                            current_price=latest_price,
                            requested_price=fallback_price,
                        )
                        logger.warning(
                            f"⚠️  CLOSE {symbol} entering fallback mode: placing reduce-only GTC at ${fallback_price:.2f} "
                            f"(latest=${latest_price:.2f}). Order will rest until filled."
                        )
                        order_result = client.place_order_with_tpsl(
                            db=db,
                            symbol=symbol,
                            is_buy=(not is_long),
                            size=close_size,
                            price=fallback_price,
                            leverage=1,
                            time_in_force="Gtc",
                            reduce_only=True,
                            take_profit_price=None,
                            stop_loss_price=None,
                            is_cross=_is_cross_margin,
                        )

                else:
                    continue

                if order_result:
                    logger.info(f"[DEBUG] {operation.upper()} order_result: {order_result}")
                    order_status = order_result.get('status')
                    order_id = order_result.get('order_id')

                    # Update decision_kwargs with order IDs for tracking (only when order succeeded)
                    if order_status in ('filled', 'resting'):
                        decision_kwargs["hyperliquid_order_id"] = order_result.get('order_id')
                        decision_kwargs["tp_order_id"] = order_result.get('tp_order_id')
                        decision_kwargs["sl_order_id"] = order_result.get('sl_order_id')

                    if order_status == 'filled':
                        logger.info(
                            f"[HYPERLIQUID] Order executed successfully for {account.name}: "
                            f"{operation.upper()} {symbol} order_id={order_id}"
                        )
                        save_ai_decision(db, account, decision, portfolio, executed=True, **decision_kwargs)

                        # Update close cooldown manager
                        if operation == "close":
                            _close_cooldown_manager.mark_closed(account.id, symbol)
                        elif operation in ("buy", "sell"):
                            # Opening new position resets close cooldown for this symbol
                            _close_cooldown_manager.reset(account.id, symbol)

                        # 开仓时记录信号快照（买入/卖出开仓，非平仓）
                        if operation in ("buy", "sell"):
                            try:
                                from backend.services.signal_feedback_tracker import signal_feedback_tracker
                                from backend.services.intelligence_signal_engine import IntelligenceSignalEngine
                                _engine = IntelligenceSignalEngine()
                                _sig = _engine.compute_trading_signal(symbol)
                                _active_sigs = {}
                                if _sig.funding:
                                    _active_sigs["funding"] = {"direction": _sig.funding.signal, "value": _sig.funding.rate}
                                if _sig.oi:
                                    _active_sigs["oi"] = {"direction": _sig.oi.signal, "value": _sig.oi.oi_change_pct}
                                if _sig.liquidation:
                                    _active_sigs["liquidation"] = {"direction": _sig.liquidation.signal, "value": 0}
                                if abs(_sig.whale_direction) > 0.1:
                                    _active_sigs["whale"] = {"direction": "bullish" if _sig.whale_direction > 0 else "bearish", "value": _sig.whale_direction}
                                if _active_sigs:
                                    # V3 整合: 计算因子快照一并记录
                                    _factor_vals = None
                                    try:
                                        from backend.services.factor_engine import factor_engine as _fe
                                        from backend.services.market_data import get_kline_data
                                        _fv_raw = get_kline_data(symbol, period="15m", count=100)
                                        if _fv_raw:
                                            import pandas as _pd
                                            _fv_df = _pd.DataFrame(_fv_raw)
                                            _fvals = _fe.compute_all_factors(_fv_df)
                                            if _fvals:
                                                _factor_vals = {k: (v.value if hasattr(v, 'value') else float(v))
                                                                for k, v in _fvals.items()}
                                    except Exception:
                                        pass
                                    signal_feedback_tracker.record_entry_signals(
                                        db, account.id, order_id, symbol, operation, _active_sigs,
                                        factor_values=_factor_vals)
                                    logger.debug(f"[HL] 信号快照已记录: order_id={order_id}")
                            except Exception as _sf_err:
                                logger.debug(f"[HL] 信号快照记录失败(非致命): {_sf_err}")

                        # 发送钉钉平仓推送通知（仅 close 操作，sell 是开空不是平仓）
                        if operation == 'close':
                            try:
                                from services.dingtalk import get_notification_service
                                import asyncio
                                import threading

                                _pos_ref = position_to_close or {}
                                _notify_data = {
                                    'symbol': symbol,
                                    'side': 'long' if _pos_ref.get('szi', 0) > 0 else 'short',
                                    'size': order_result.get('filled_amount', 0),
                                    'entry_price': float(_pos_ref.get('entryPx', 0)),
                                    'exit_price': order_result.get('average_price', 0),
                                    'pnl': float(_pos_ref.get('unrealizedPnl', 0)),
                                    'pnl_percent': 0,
                                    'hold_duration': 'N/A',
                                    'account_name': account.name,
                                    'exchange': 'Hyperliquid',
                                    'position_id': '',
                                    'order_id': str(order_id)
                                }

                                def send_close_notification(_data=_notify_data):
                                    try:
                                        loop = asyncio.new_event_loop()
                                        asyncio.set_event_loop(loop)
                                        notification_service = get_notification_service(db)
                                        loop.run_until_complete(
                                            notification_service.notify_position_closed(
                                                account_id=account.id,
                                                position_data=_data
                                            )
                                        )
                                        loop.close()
                                    except Exception as e:
                                        logger.error(f"Failed to send close position notification: {e}")

                                notification_thread = threading.Thread(target=send_close_notification, daemon=True)
                                notification_thread.start()

                            except Exception as e:
                                logger.error(f"Failed to trigger close position notification: {e}")

                        try:
                            from backend.database.snapshot_connection import SnapshotSessionLocal
                            from backend.database.snapshot_models import HyperliquidTrade
                            from decimal import Decimal

                            snapshot_db = SnapshotSessionLocal()
                            try:
                                trade_record = HyperliquidTrade(
                                    account_id=account.id,
                                    environment=environment,
                                    wallet_address=wallet_address,
                                    symbol=symbol,
                                    side=operation,
                                    quantity=Decimal(str(order_result.get('filled_amount', 0))),
                                    price=Decimal(str(order_result.get('average_price', 0))),
                                    leverage=leverage,
                                    order_id=order_id,
                                    order_status=order_status,
                                    trade_value=Decimal(str(order_result.get('filled_amount', 0))) * Decimal(str(order_result.get('average_price', 0))),
                                    fee=Decimal(str(order_result.get('fee', 0)))
                                )
                                snapshot_db.add(trade_record)
                                snapshot_db.commit()
                                logger.info(f"[HYPERLIQUID] Trade record saved for {account.name}")
                            finally:
                                snapshot_db.close()
                        except Exception as trade_err:
                            logger.warning(f"Failed to save Hyperliquid trade record: {trade_err}")

                        # 平仓成交后通知学习系统
                        if operation == "close":
                            try:
                                from backend.services.unified_learning_service import unified_learning, TradeOutcome
                                _entry_px = float(position_to_close.get("entryPx", 0)) if position_to_close else 0
                                _exit_px = float(order_result.get("average_price", 0))
                                _sz = float(order_result.get("filled_amount", 0))
                                _pnl_est = (_exit_px - _entry_px) * _sz if is_long else (_entry_px - _exit_px) * _sz
                                _pnl_pct = _pnl_est / (_entry_px * _sz) if _entry_px * _sz > 0 else 0
                                _tpl_id = decision_kwargs.get("prompt_template_id") or ""
                                _strat_id = (trigger_context.get("strategy_id") if trigger_context else None) or ""
                                _tier = (trigger_context.get("tier") if trigger_context else None) or "mid"

                                # 尝试获取真实 regime（而非 "unknown"）+ TrendState 增强
                                _regime = "unknown"
                                _adx_at_entry = 0.0
                                _trend_dir = "neutral"
                                _trend_str = "none"
                                try:
                                    # [2026-08-17 修复] 原 `from ...strategy_coordinator import
                                    # coordinator` 单例不存在 → ImportError 被 except 静默吞掉，
                                    # regime 富化永远 unknown。改为按需构造（用完即关连接）。
                                    from backend.database.connection import SessionLocal
                                    from backend.services.strategy_coordinator import StrategyCoordinator
                                    import time as _time
                                    _now_ts = int(_time.time())
                                    _start_ts = _now_ts - 30 * 86400
                                    _cdb = SessionLocal()
                                    try:
                                        _klines = StrategyCoordinator(_cdb)._query_klines(
                                            symbol, "1h", _start_ts, _now_ts, "hyperliquid",
                                        )
                                    finally:
                                        _cdb.close()
                                    if _klines and len(_klines) >= 60:
                                        from backend.services.strategy_fingerprint import compute_fingerprint_from_live
                                        _fp_data = {
                                            "closes": [k["close"] for k in _klines],
                                            "highs": [k["high"] for k in _klines],
                                            "lows": [k["low"] for k in _klines],
                                            "volumes": [k["volume"] for k in _klines],
                                        }
                                        _fp = compute_fingerprint_from_live(_fp_data)
                                        _regime = _fp.regime
                                except Exception:
                                    pass
                                # TrendState 增强 regime
                                try:
                                    from backend.services.unified_data_pool import UnifiedDataPool
                                    from backend.services.trend_classifier import classify_from_indicators, classify_market_environment
                                    _snap = UnifiedDataPool().get_snapshot(max_age=120)
                                    if _snap and symbol in _snap.indicators:
                                        _ind = _snap.indicators[symbol]
                                        _kl = _snap.klines
                                        _ts1d = classify_from_indicators(_ind, _kl, "1d", symbol)
                                        _ts4h = classify_from_indicators(_ind, _kl, "4h", symbol)
                                        _adx_at_entry = _ind.get("adx_4h", _ind.get("adx", 0))
                                        _trend_dir = _ts4h.direction
                                        _trend_str = _ts4h.strength
                                        _env = classify_market_environment(_ts1d)
                                        if _env == "strong_trend":
                                            _regime = f"strong_trend_{_ts1d.direction}"
                                        elif _env == "weak_trend":
                                            _regime = f"weak_trend_{_ts4h.direction}"
                                        elif _env == "volatile":
                                            _regime = "volatile"
                                        elif _env == "ranging":
                                            _regime = "ranging"
                                except Exception:
                                    pass

                                # 获取 decision_log_id（供智慧评估闭环使用）
                                _decision_log_id = decision_kwargs.get("decision_log_id") or decision.get("_decision_log_id") or ""

                                # 深挖第 1 项修复 (2026-05-08)： #   live 路径之前 duration_seconds=0、opened_at 取不到 → StrategyTrade 持仓周期不可信。 #   现在按 orders.created_at → HyperliquidPosition.snapshot_time 顺序反推真实开仓时间。
                                _live_opened_at = None
                                _live_duration = 0
                                _opened_at_source = "unknown"
                                try:
                                    from backend.database.models import (
                                        Order as _Order, HyperliquidPosition as _HLP,
                                    )
                                    from datetime import datetime as _dt, timezone as _tz
                                    from sqlalchemy import or_ as _or
                                    _open_side = "buy" if is_long else "sell"
                                    _earliest_order = (
                                        db.query(_Order)
                                        .filter(
                                            _Order.account_id == account.id,
                                            _Order.symbol == symbol,
                                            _Order.side == _open_side,
                                            _Order.status.in_(["filled", "FILLED", "executed"]),
                                            _or(
                                                _Order.reduce_only.is_(None),
                                                _Order.reduce_only != "true",
                                            ),
                                        )
                                        .order_by(_Order.created_at.asc())
                                        .first()
                                    )
                                    if _earliest_order and _earliest_order.created_at:
                                        _live_opened_at = _earliest_order.created_at
                                        _opened_at_source = "order"
                                    else:
                                        _earliest_pos = (
                                            db.query(_HLP)
                                            .filter(
                                                _HLP.account_id == account.id,
                                                _HLP.symbol == symbol,
                                            )
                                            .order_by(_HLP.snapshot_time.asc())
                                            .first()
                                        )
                                        if _earliest_pos and _earliest_pos.snapshot_time:
                                            _live_opened_at = _earliest_pos.snapshot_time
                                            _opened_at_source = "snapshot"
                                    if _live_opened_at:
                                        _now = _dt.now(_tz.utc)
                                        _opened_aware = (
                                            _live_opened_at.replace(tzinfo=_tz.utc)
                                            if _live_opened_at.tzinfo is None
                                            else _live_opened_at
                                        )
                                        _live_duration = max(0, int((_now - _opened_aware).total_seconds()))
                                except Exception as _open_err:
                                    logger.debug(f"[LEARNING] live opened_at 反推失败: {_open_err}")

                                live_outcome = TradeOutcome(
                                    source="live",
                                    strategy_id=_strat_id,
                                    template_id=str(_tpl_id) if _tpl_id else "",
                                    symbol=symbol,
                                    side="long" if is_long else "short",
                                    tier=_tier,
                                    entry_price=_entry_px,
                                    exit_price=_exit_px,
                                    pnl=_pnl_est,
                                    pnl_pct=_pnl_pct,
                                    duration_seconds=_live_duration,
                                    regime_at_entry=_regime,
                                    regime_at_exit=_regime,
                                    confidence=0.6,
                                    position_size=_sz,
                                    opened_at=_live_opened_at,
                                    metadata={
                                        "close_reason": "ai_close",
                                        "tier": _tier,
                                        "decision_log_id": _decision_log_id,
                                        "leverage": leverage,
                                        "adx_at_entry": round(_adx_at_entry, 1),
                                        "trend_direction": _trend_dir,
                                        "trend_strength": _trend_str,
                                        "opened_at_source": _opened_at_source,
                                    },
                                )
                                unified_learning.process_outcome(db, live_outcome)
                                logger.info(f"[LEARNING] Live close outcome sent: {symbol} pnl={_pnl_est:.2f} regime={_regime}")

                                # L2 收敛: process_outcome 内部已自动调度全部学习后端 # （含原 LearningBus 的 review/miner/pattern/causal_discovery）。 # 不再手动调用 bus.dispatch，避免双入口顺序契约。

                                # 反馈信号权重
                                try:
                                    from backend.services.signal_feedback_tracker import signal_feedback_tracker
                                    _trade_id = order_result.get("order_id") or order_result.get("oid")
                                    if _trade_id:
                                        signal_feedback_tracker.update_trade_pnl(db, _trade_id, _pnl_est, _pnl_pct)
                                except Exception:
                                    pass

                            except Exception as _learn_err:
                                logger.warning(f"[LEARNING] Failed to process live outcome: {_learn_err}")

                    elif order_status == 'resting':
                        logger.info(
                            f"[HYPERLIQUID] Order placed (resting) for {account.name}: "
                            f"{operation.upper()} {symbol} order_id={order_id}"
                        )
                        save_ai_decision(db, account, decision, portfolio, executed=True, **decision_kwargs)

                    else:
                        error_msg = order_result.get('error', 'Unknown error')
                        logger.error(
                            f"[HYPERLIQUID] Order failed for {account.name}: "
                            f"{operation.upper()} {symbol} - {error_msg}"
                        )
                        save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)
                else:
                    logger.error(f"No order result received for {account.name}")
                    save_ai_decision(db, account, decision, portfolio, executed=False, **decision_kwargs)

        except Exception as account_err:
            logger.error(f"Error processing Hyperliquid account {account.name}: {account_err}", exc_info=True)
            db.rollback()
        finally:
            db.close()


HYPERLIQUID_TRADE_JOB_ID = "hyperliquid_ai_trade"


def execute_hyperliquid_close_decisions(
    db: Session,
    account_id: int,
    decisions: List[Dict[str, Any]],
) -> None:
    """Execute close (or partial close) orders on HyperLiquid. Used by PositionTracker for emergency exit / partial close.

    Each decision must have:
      - operation: "sell" (close long) or "buy" (close short)
      - symbol: e.g. "BTC"
      - quantity: optional; if absent, target_portion_of_balance 0 means full close
      - target_portion_of_balance: 0 = close full position; (0,1] = close that fraction
    """
    if not decisions:
        return
    try:
        from services.hyperliquid_environment import get_hyperliquid_client
    except Exception as e:
        logger.error(f"execute_hyperliquid_close_decisions: get_hyperliquid_client failed: {e}", exc_info=True)
        return

    account = db.query(Account).filter(Account.id == account_id).first()
    if not account or getattr(account, "hyperliquid_enabled", "false") != "true":
        logger.debug(f"Account {account_id} not found or HyperLiquid not enabled, skip close decisions")
        return

    client = get_hyperliquid_client(db, account_id)
    if not client:
        logger.warning(f"execute_hyperliquid_close_decisions: no HyperLiquid client for account_id={account_id}")
        return

    # Read global margin mode
    _close_is_cross = False
    try:
        _mc = db.query(SystemConfig).filter(SystemConfig.key == "global_margin_mode").first()
        if _mc and _mc.value == "cross":
            _close_is_cross = True
    except Exception:
        pass

    positions = client.get_positions(db, include_timing=False) or []
    prices = _get_market_prices(list({d.get("symbol") for d in decisions if d.get("symbol")}))

    for decision in decisions:
        symbol = decision.get("symbol")
        operation = (decision.get("operation") or "").lower()
        if not symbol or operation not in ("buy", "sell"):
            continue

        position_to_close = None
        for pos in positions:
            if pos.get("coin") == symbol:
                position_to_close = pos
                break
        if not position_to_close:
            logger.warning(f"execute_hyperliquid_close_decisions: no position for {symbol}, skip")
            continue

        position_size = abs(float(position_to_close.get("szi", 0)))
        is_long = (position_to_close.get("szi", 0) or 0) > 0
        if position_size <= 0:
            continue

        # Close size: explicit quantity or by ratio
        close_ratio = float(decision.get("target_portion_of_balance") or 0)
        if close_ratio <= 0:
            close_ratio = 1.0
        if "quantity" in decision and decision["quantity"] is not None:
            close_size = float(decision["quantity"])
        else:
            close_size = round(position_size * close_ratio, 6)
        close_size = min(close_size, position_size)
        if close_size <= 0:
            continue

        # reduce_only: direction opposite to position (sell to close long, buy to close short)
        is_buy_close = not is_long
        current_price = prices.get(symbol, 0) or 0
        if current_price <= 0:
            try:
                current_price = float(get_last_price(symbol, "CRYPTO"))
            except Exception:
                current_price = 0
        if current_price <= 0:
            logger.warning(f"execute_hyperliquid_close_decisions: no price for {symbol}, skip")
            continue

        close_price = current_price * (1.005 if is_buy_close else 0.995)
        close_price, _, _ = _enforce_price_bounds(
            symbol=symbol,
            account_name=account.name or str(account_id),
            operation="close",
            current_price=current_price,
            requested_price=close_price,
        )

        try:
            order_result = client.place_order_with_tpsl(
                db=db,
                symbol=symbol,
                is_buy=is_buy_close,
                size=close_size,
                price=close_price,
                leverage=1,
                time_in_force="Ioc",
                reduce_only=True,
                take_profit_price=None,
                stop_loss_price=None,
                is_cross=_close_is_cross,
            )
            if order_result and order_result.get("status") in ("filled", "resting"):
                logger.info(
                    f"[PositionTracker] HyperLiquid close executed: {symbol} size={close_size} "
                    f"({'buy' if is_buy_close else 'sell'}) result={order_result.get('status')}"
                )
            elif order_result and order_result.get("status") == "error":
                logger.warning(
                    f"[PositionTracker] HyperLiquid close failed: {symbol} error={order_result.get('error')}"
                )
        except Exception as e:
            logger.error(f"execute_hyperliquid_close_decisions: place_order failed {symbol}: {e}", exc_info=True)


def place_ai_driven_binance_order(
    account_ids: Optional[Iterable[int]] = None,
    account_id: Optional[int] = None,
    bypass_auto_trading: bool = False,
    trigger_context: Optional[Dict[str, Any]] = None,
    samples: Optional[List[Dict]] = None,
) -> None:
    """Stub: Binance removed (Phase 1). Use HyperLiquid (place_ai_driven_hyperliquid_order) instead."""
    logger.debug("place_ai_driven_binance_order called but Binance has been removed (Phase 1)")
    return


# (place_ai_driven_binance_order implementation removed - Binance Phase 1)


BINANCE_TRADE_JOB_ID = "binance_ai_trade"


# ══════════════════════════════════════════════════════════════════
#  Unified AI-Driven Order — Multi-Exchange Entry Point
# ══════════════════════════════════════════════════════════════════

def _close_ccxt_client(client) -> None:
    """关闭 fresh ccxt 客户端（aiohttp 会话泄漏防护；失败静默）。"""
    try:
        import asyncio as _aio
        _close_ex = getattr(client, "close", None)
        if callable(_close_ex):
            _aio.run(_aio.wait_for(_close_ex(), timeout=5))
    except Exception:
        pass


def place_ai_driven_order(
    account_id: Optional[int] = None,
    bypass_auto_trading: bool = False,
    trigger_context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Unified AI trading entry point — routes to the correct exchange
    based on the account's `selected_exchange` field.

    - Hyperliquid: delegates to place_ai_driven_hyperliquid_order()
    - CCXT exchanges (binance/bybit/okx/gateio/asterdex): delegates to _execute_ccxt_ai_trade()
    """
    if account_id is None:
        logger.warning("place_ai_driven_order: account_id is required")
        return

    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            logger.warning("place_ai_driven_order: account %d not found", account_id)
            return
        # 运行时默认交易所（账户字段缺失时的兜底；新账户默认 asterdex）
        try:
            from backend.config.settings import DEFAULT_EXCHANGE as _DEFAULT_EX
        except Exception:
            _DEFAULT_EX = "asterdex"
        selected_exchange = getattr(account, "selected_exchange", _DEFAULT_EX) or _DEFAULT_EX
    finally:
        db.close()

    if selected_exchange == "hyperliquid":
        logger.info("[UnifiedRouter] Account %d → Hyperliquid", account_id)
        place_ai_driven_hyperliquid_order(
            account_id=account_id,
            bypass_auto_trading=bypass_auto_trading,
            trigger_context=trigger_context,
        )
    else:
        logger.info("[UnifiedRouter] Account %d → %s (CCXT)", account_id, selected_exchange)
        _execute_ccxt_ai_trade(
            account_id=account_id,
            exchange=selected_exchange,
            bypass_auto_trading=bypass_auto_trading,
            trigger_context=trigger_context,
        )


def _execute_ccxt_ai_trade(
    account_id: int,
    exchange: str,
    bypass_auto_trading: bool = False,
    trigger_context: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Execute AI-driven trade on a CCXT-based exchange.

    Follows the same high-level flow as place_ai_driven_hyperliquid_order():
    1. Load account & validate
    2. Get exchange client (via global credentials)
    3. Fetch balance & positions
    4. Call AI for decision
    5. Risk control checks
    6. Execute orders via BaseExchangeClient
    7. Save decision logs
    """
    import asyncio
    from backend.services.exchange.exchange_manager import get_exchange_manager
    from backend.services.exchange.base_exchange_client import (
        OrderSide, OrderType, ExchangeOrder, ExchangeBalance, ExchangePosition,
    )

    # ── 1. Load account ──
    db = SessionLocal()
    try:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account or account.is_active != "true":
            logger.debug("Account %d not found or inactive", account_id)
            return
        if not bypass_auto_trading and getattr(account, "auto_trading_enabled", "false") != "true":
            logger.debug("Account %s auto trading disabled", account.name)
            return

        # Resolve LLM config
        resolve_account_llm_config(db, account)

        # [2026-08-29 实盘零成交·末环修复] 预生成决策(pre_made_decisions)由
        # 系统车道（因子/Agent/MLTO）完整产出，不需要 LLM 参与——此前仍
        # 强制账户级 LLM 配置(api_key/model)，实盘账户未配 LLM → 订单在
        # UnifiedRouter 之后被"LLM配置不完整"静默跳过（实测 18:45 ETH）。
        # 仅对"无预生成决策、需现场调 LLM"的路径保留该硬检查。
        _has_pre_made = bool(
            isinstance(trigger_context, dict)
            and trigger_context.get("pre_made_decisions")
        )
        if not _has_pre_made and (not account.api_key or not account.model):
            logger.warning(
                "AI交易员 '%s' (ID: %d) 已跳过 - LLM配置不完整",
                account.name, account.id,
            )
            return

        # Check strategy
        from backend.database.models import AccountStrategyConfig
        strategy = db.query(AccountStrategyConfig).filter(
            AccountStrategyConfig.account_id == account.id,
            AccountStrategyConfig.enabled == "true",
        ).first()
        if not strategy:
            # 预生成决策自带策略上下文，同样不应被账户级策略配置卡死
            if not _has_pre_made:
                logger.warning("Account %s: strategy not configured or disabled", account.name)
                return
            logger.info(
                "[PreMade] Account %s 无账户级策略配置，使用预生成决策继续",
                account.name,
            )

    finally:
        db.close()

    # ── 2. Get exchange client ──
    mgr = get_exchange_manager()
    # [2026-08-28] 账户绑定凭证优先,无绑定则回退全局凭证(user-level)
    user_id = account.user_id or 1
    # [2026-08-28 环境] 实盘按账户币安环境建客户端（usdt_m/coin_m/margin）
    _mt = getattr(account, "binance_market_type", None) or "usdt_m"
    # [2026-08-29 实盘下单挂死修复] 缓存客户端的 aiohttp 会话绑定在首次调用
    # 的 event loop 上，跨 asyncio.run 复用会永久挂起（实测 18:52 ETH 下单后
    # live 会话循环 62s→182s 递增卡死，无任何日志返回）。改为：
    # ① create_fresh_client 每次新建（与 live_executor 同款根治方案）；
    # ② wait_for 20s 硬超时，超时/异常诚实报错返回；③ 用完即关。
    client = mgr.create_fresh_client(
        exchange, user_id=user_id, account_id=account.id,
        market_type=_mt if exchange == "binance" else None,
    )
    if client is None:
        client = mgr.get_or_create_global_client(
            exchange, user_id=user_id, account_id=account.id,
            market_type=_mt if exchange == "binance" else None,
        )
    if client is None:
        logger.warning(
            "AI交易员 '%s': %s 交易所未配置全局API凭证，请先在「交易所配置」中添加",
            account.name, exchange,
        )
        return

    # ── 3. Get balance & positions ──
    async def _fetch_bal_pos():
        _bal = await asyncio.wait_for(client.get_balance(), timeout=20)
        _pos = await asyncio.wait_for(client.get_positions(), timeout=20)
        return _bal, _pos

    try:
        balance, positions = asyncio.run(_fetch_bal_pos())
    except Exception as e:
        logger.error("Failed to get %s balance/positions for %s: %s", exchange, account.name, e)
        _close_ccxt_client(client)
        return
    # 注意：client 后续下单仍要用，函数末尾统一 _close_ccxt_client(client)

    available_balance = balance.available_balance
    total_equity = balance.total_equity
    margin_usage = balance.margin_ratio * 100

    logger.info(
        "[%s] Account %s: equity=$%.2f, available=$%.2f, margin=%.1f%%, positions=%d",
        exchange.upper(), account.name, total_equity, available_balance, margin_usage, len(positions),
    )

    if total_equity <= 0 and len(positions) == 0:
        logger.warning("Account %s: no balance to trade on %s", account.name, exchange)
        return

    # ── 4. Build portfolio data ──
    portfolio = {
        "cash": available_balance,
        "frozen_cash": balance.frozen_margin,
        "positions": {},
        "total_assets": total_equity,
    }
    for pos in positions:
        portfolio["positions"][pos.symbol] = {
            "quantity": pos.size if pos.side == "long" else -pos.size,
            "avg_cost": pos.entry_price,
            "current_value": pos.size * pos.mark_price,
            "unrealized_pnl": pos.unrealized_pnl,
            "leverage": pos.leverage,
        }

    exchange_state = {
        "exchange": exchange,
        "total_equity": total_equity,
        "available_balance": available_balance,
        "used_margin": balance.frozen_margin,
        "margin_usage_percent": margin_usage,
        "positions": [
            {
                "symbol": p.symbol, "side": p.side, "size": p.size,
                "entry_price": p.entry_price, "mark_price": p.mark_price,
                "unrealized_pnl": p.unrealized_pnl, "leverage": p.leverage,
            }
            for p in positions
        ],
    }

    # ── 5. Get symbols & prices ──
    # Default symbols for CCXT exchanges
    from backend.services.trading_pairs_config import get_user_trading_pairs
    selected_symbols = get_user_trading_pairs()
    prices = _get_market_prices(selected_symbols)
    if not prices:
        logger.warning("Failed to get market prices, skipping %s trading", exchange)
        return

    # ── 6. Risk control - daily loss check ──
    risk_service = get_risk_control_service()
    risk_service.load_config_from_db(db, account.id)
    daily_loss_check = risk_service.check_daily_loss_breaker(db, account.id, total_equity)
    if daily_loss_check.result == RiskCheckResult.BLOCKED:
        logger.warning("[RISK] Account %s blocked by circuit breaker: %s", account.name, daily_loss_check.message)
        return

    # ── 7. Get AI decision ──
    db = SessionLocal()
    try:
        pre_made = (trigger_context.get("pre_made_decisions") if trigger_context else None)
        if pre_made:
            decisions = pre_made
            logger.info("[%s] Using %d pre-made decisions for %s", exchange.upper(), len(decisions), account.name)
        else:
            decisions = call_ai_for_decision(
                db,
                account,
                portfolio,
                prices,
                symbols=selected_symbols,
                hyperliquid_state=exchange_state,
                symbol_metadata=None,
                trigger_context=trigger_context,
            )

        if not decisions:
            logger.warning("No AI decision for %s on %s", account.name, exchange)
            return

        # ── 8. Process decisions ──
        decision_priority = {"close": 0, "sell": 1, "buy": 2, "hold": 3}
        ordered_decisions = sorted(
            decisions,
            key=lambda d: decision_priority.get(str(d.get("operation", "")).lower(), 4),
        )

        symbol_whitelist = set(selected_symbols)

        for decision in ordered_decisions:
            if not isinstance(decision, dict):
                continue

            operation = decision.get("operation", "").lower()
            symbol = decision.get("symbol", "").upper()
            # [2026-08-31 白名单格式修复] 决策符号归一为裸基币名。
            # 白名单/价格表以裸名("ASTER")为键，而上游（LiveExecutor 平仓/
            # LPM/对账）可能传统一符号("ASTER/USDT:USDT") → 此前被静默拒单
            # （save_ai_decision executed=False）而 LiveExecutor 乐观报 filled，
            # 实盘单黑洞。ccxt 适配器下单时自行做裸名→统一符号解析。
            if "/" in symbol:
                symbol = symbol.split("/")[0].strip()
                decision["symbol"] = symbol
            target_portion = float(decision.get("target_portion_of_balance", 0))
            leverage = int(decision.get("leverage", 10))
            # 同币已有仓：adopt 交易所杠杆（一仓一杠杆）
            try:
                from backend.services.leverage_authority import extract_existing_symbol_leverage
                _adopt_lev = extract_existing_symbol_leverage(symbol, positions)
                if _adopt_lev is not None:
                    leverage = max(1, int(round(float(_adopt_lev))))
                    decision["leverage"] = leverage
            except Exception:
                pass
            reason = decision.get("reason", "No reason provided")
            max_price = decision.get("max_price")
            min_price = decision.get("min_price")

            # Validate fields
            decision, autofill_warnings = _validate_and_autofill_decision(decision, prices, symbol_whitelist)
            operation = decision.get("operation", "").lower()
            symbol = decision.get("symbol", "").upper()

            # Confidence check
            raw_conf = decision.get("confidence", 1.0)
            confidence = _normalize_confidence(raw_conf)
            if confidence < 0.6 and operation in ("buy", "sell"):
                logger.warning("[RiskGuard] Confidence %.2f < 0.6, downgrading to HOLD: %s", confidence, symbol)
                decision["operation"] = "hold"
                operation = "hold"

            # Position cap 20%
            if target_portion > 0.20:
                target_portion = 0.20
                decision["target_portion_of_balance"] = 0.20

            # Leverage range 5x-20x
            if leverage > 20:
                leverage = 20
            elif leverage < 5:
                leverage = 5

            if operation == "hold":
                save_ai_decision(db, account, decision, portfolio, executed=True)
                continue

            if operation not in ("buy", "sell", "close"):
                save_ai_decision(db, account, decision, portfolio, executed=False)
                continue

            if symbol not in symbol_whitelist:
                logger.warning("Symbol '%s' not in whitelist for %s", symbol, account.name)
                save_ai_decision(db, account, decision, portfolio, executed=False)
                continue

            price = prices.get(symbol)
            if not price or price <= 0:
                save_ai_decision(db, account, decision, portfolio, executed=False)
                continue

            # ── [2026-08-31 平仓修复] reduce_only 显式数量快速通道 ──
            # LiveExecutor.close_position/_send_raw_order 传统量+reduce_only 走
            # operation=sell 的决策，而 sell 分支按余额×portion 重算名义且
            # reduce_only=False → 实盘"平仓"从未真正平过仓（数量0或反向开仓）。
            # 决策现透传 quantity/reduce_only：命中时发精确数量的 reduce-only
            # 市价单，不再进开仓 sizing 分支，也不受可用余额下限约束。
            if (
                operation in ("buy", "sell", "close")
                and bool(decision.get("reduce_only"))
                and float(decision.get("quantity") or 0) > 0
            ):
                _rq = float(decision.get("quantity"))
                # 方向推导：显式 close_position_side（"long"/"short"）优先；
                # close 按本地持仓方向反推；其余按 operation 本身（sell=平多, buy=平空）。
                _side_hint = str(decision.get("close_position_side") or "").strip().lower()
                if not _side_hint and operation == "close":
                    _pos_match = next(
                        (p for p in positions
                         if str(getattr(p, "symbol", "") or "").upper().split("/")[0]
                         == symbol.split("/")[0]),
                        None,
                    )
                    if _pos_match is not None:
                        _side_hint = "short" if str(
                            getattr(_pos_match, "side", "long")).lower() == "short" else "long"
                if _side_hint == "short":
                    _r_side = OrderSide.BUY   # 平空：买入
                elif _side_hint == "long":
                    _r_side = OrderSide.SELL  # 平多：卖出
                else:
                    _r_side = OrderSide.SELL if operation == "sell" else OrderSide.BUY
                try:
                    # [2026-08-31 挂死修复] 新客户端 + 20s 硬超时（同余额/持仓
                    # 的 8/29 修复模式；旧 asyncio.run(client.place_order) 在
                    # 余额查询之后第二次进新事件循环会永久挂起）。
                    _r_result = _place_order_fresh_client(
                        mgr, exchange, user_id, account.id, _mt,
                        ExchangeOrder(
                            order_id="", symbol=symbol,
                            side=_r_side, order_type=OrderType.MARKET,
                            size=_rq, price=price, leverage=1, reduce_only=True,
                            # [2026-08-31 Hedge] 币安双向持仓模式平仓必须带
                            # positionSide=被平仓位方向（平多=SELL+LONG）。
                            position_side=(
                                ("LONG" if _side_hint == "long" else "SHORT")
                                if _side_hint in ("long", "short") else None
                            ),
                        ),
                    )
                    logger.info(
                        "[%s] REDUCE-ONLY %s executed: side=%s qty=%.6f result=%s",
                        exchange.upper(), symbol, _r_side.value, _rq, _r_result,
                    )
                    save_ai_decision(db, account, decision, portfolio, executed=True)
                except Exception as e:
                    logger.error("[%s] REDUCE-ONLY %s failed: %s", exchange.upper(), symbol, e)
                    decision["error_reason"] = str(e)
                    save_ai_decision(db, account, decision, portfolio, executed=False)
                continue

            if operation == "close":
                # Find position to close
                # [2026-08-31 格式容忍] positions 可能带统一符号("ASTER/USDT:USDT")
                # 而决策符号是裸名——按裸基币名匹配。
                pos_to_close = next(
                    (p for p in positions
                     if str(getattr(p, "symbol", "") or "").upper().split("/")[0]
                     == symbol.split("/")[0]),
                    None,
                )
                if not pos_to_close:
                    logger.warning("No position to close for %s on %s", symbol, exchange)
                    save_ai_decision(db, account, decision, portfolio, executed=False)
                    continue

                close_size = abs(pos_to_close.size)
                is_buy_close = pos_to_close.side == "short"
                close_price = price

                order = ExchangeOrder(
                    order_id="",
                    symbol=symbol,
                    side=OrderSide.BUY if is_buy_close else OrderSide.SELL,
                    order_type=OrderType.MARKET,
                    size=close_size,
                    price=close_price,
                    leverage=1,
                    reduce_only=True,
                )

                try:
                    result = _place_order_fresh_client(
                        mgr, exchange, user_id, account.id, _mt, order)
                    logger.info(
                        "[%s] CLOSE %s executed: side=%s size=%.4f result=%s",
                        exchange.upper(), symbol, "buy" if is_buy_close else "sell",
                        close_size, result,
                    )
                    save_ai_decision(db, account, decision, portfolio, executed=True)
                except Exception as e:
                    logger.error("[%s] CLOSE %s failed: %s", exchange.upper(), symbol, e)
                    decision["error_reason"] = str(e)
                    save_ai_decision(db, account, decision, portfolio, executed=False)
                continue

            # buy / sell
            MIN_AVAILABLE_BALANCE = 10
            if available_balance < MIN_AVAILABLE_BALANCE:
                logger.warning("Available balance $%.2f < $%d minimum on %s", available_balance, MIN_AVAILABLE_BALANCE, exchange)
                save_ai_decision(db, account, decision, portfolio, executed=False)
                continue

            margin = available_balance * target_portion
            order_value = margin * leverage
            quantity = round(order_value / price, 6)

            # [2026-09-03 v3 方向1] PositionConstruction 单一权威：AI 的 portion×leverage 只是提议
            order_value, leverage, quantity, _pc_block = _v3_position_construction_live(
                db, account, symbol, price=price, order_value=order_value, leverage=leverage,
                total_equity=total_equity, decision=decision, exchange=exchange,
            )
            if _pc_block:
                decision["error_reason"] = _pc_block
                save_ai_decision(db, account, decision, portfolio, executed=False)
                continue
            margin = order_value / max(1.0, float(leverage or 1.0))

            # [2026-09-03 v3 方向7] RiskEngine 单入口（急停/状态机/连通性/避险窗口/日配额）
            _v3_block = _v3_risk_pre_trade_live(
                db, account, symbol, operation, order_value=order_value, total_equity=total_equity,
                leverage=leverage, exchange=exchange, decision=decision,
            )
            if _v3_block:
                decision["error_reason"] = _v3_block
                save_ai_decision(db, account, decision, portfolio, executed=False)
                continue

            # Risk check
            risk_allowed, risk_message = check_risk_before_trade(
                db=db, account_id=account.id, symbol=symbol, operation=operation,
                order_value=order_value, total_equity=total_equity,
                available_balance=available_balance, positions=[],
                margin_usage_percent=margin_usage,
            )
            if not risk_allowed:
                logger.warning("[RISK] %s order blocked: %s", exchange.upper(), risk_message)
                decision["error_reason"] = risk_message
                save_ai_decision(db, account, decision, portfolio, executed=False)
                continue

            # [2026-09-03 v3 F2] 账户级日手续费预算门（live：进程内按日累计的成交费用估算）
            try:
                from backend.services.ledger.fee_budget import (
                    check_fee_budget as _fee_budget_check,
                    fees_paid_today_live as _fees_today_live,
                )
                _fb = _fee_budget_check(
                    db, account.id, est_notional=float(order_value or 0), equity=float(total_equity or 0),
                    fees_today=_fees_today_live(account.id), source="live",
                )
                if not _fb.allowed:
                    logger.warning("[FeeBudget] %s %s %s 拦截: %s", exchange.upper(), symbol, operation, _fb.reason)
                    decision["error_reason"] = _fb.reason
                    save_ai_decision(db, account, decision, portfolio, executed=False)
                    continue
            except Exception as _fb_err:
                logger.debug("[FeeBudget] live 检查异常(放行): %s", _fb_err)

            # Use max_price/min_price if provided
            order_price = price
            if operation == "buy" and max_price:
                order_price = max_price
            elif operation == "sell" and min_price:
                order_price = min_price

            # 阶段 3.2: OrderAlgo 切片（默认 MARKET 单笔，行为不变）
            algo = str(decision.get("algo", "MARKET") or "MARKET").upper()
            # [2026-09-03 Aster 费率优化] 仅中长线开仓走 maker 优先（其余原样市价）
            _mf_on, _mf_timeout = _live_maker_first_decision(exchange, decision, trigger_context)
            try:
                if algo == "MARKET" or quantity <= 0:
                    result = _place_order_fresh_client(
                        mgr, exchange, user_id, account.id, _mt,
                        ExchangeOrder(
                            order_id="", symbol=symbol,
                            side=OrderSide.BUY if operation == "buy" else OrderSide.SELL,
                            order_type=OrderType.MARKET, size=quantity, price=order_price,
                            sl=decision.get("stop_loss_price"),
                            tp=decision.get("take_profit_price"),
                            leverage=leverage, reduce_only=False,
                        ),
                        maker_first=_mf_on, maker_timeout_s=_mf_timeout,
                    )
                else:
                    from backend.services.exchange.algo_exec import build_algo_slices, execute_slices
                    children, meta = build_algo_slices(
                        quantity, algo, decision.get("algo_config"))
                    if not children:
                        raise RuntimeError(f"algo {algo} 切片为空")
                    if meta.get("fallback"):
                        logger.warning(
                            f"[AlgoExec][CCXT:{algo}] {symbol} {operation} 降级: {meta['fallback']}"
                        )

                    def _place_ccxt_slice(qty: float, is_last: bool):
                        # 仅最后一片携带 TP/SL（避免重复触发单）
                        _o = ExchangeOrder(
                            order_id="", symbol=symbol,
                            side=OrderSide.BUY if operation == "buy" else OrderSide.SELL,
                            order_type=OrderType.MARKET, size=qty, price=order_price,
                            sl=(decision.get("stop_loss_price") if is_last else None),
                            tp=(decision.get("take_profit_price") if is_last else None),
                            leverage=leverage, reduce_only=False,
                        )
                        _r = _place_order_fresh_client(
                            mgr, exchange, user_id, account.id, _mt, _o,
                            maker_first=_mf_on, maker_timeout_s=_mf_timeout)
                        logger.info(
                            f"[AlgoExec][CCXT:{algo}] slice {'LAST' if is_last else '..'} "
                            f"{symbol} {operation} qty={qty} -> {_r}"
                        )
                        return _r

                    out = execute_slices(
                        children, _place_ccxt_slice,
                        log_prefix=f"[AlgoExec][CCXT:{algo}]", sleep_fn=time.sleep,
                    )
                    if not out["results"]:
                        raise RuntimeError(f"algo {algo} 全部子单失败: {out['errors']}")
                    result = out["results"][-1]
                    logger.info(
                        "[%s] %s %s algo=%s slices=%d/%d executed (last=%s)",
                        exchange.upper(), operation.upper(), symbol, algo,
                        out["completed"], out["total"], result,
                    )

                save_ai_decision(db, account, decision, portfolio, executed=True)
                # [2026-09-03 v3 F2] 成交后登记本单预估手续费到日预算累计（taker 口径，偏保守）
                try:
                    from backend.services.ledger.fee_budget import estimate_fee as _est_fee, record_live_fee as _rec_fee
                    _rec_fee(account.id, _est_fee(float(order_value or 0)))
                except Exception:
                    pass
                # Update balance for subsequent decisions
                available_balance -= margin
            except Exception as e:
                logger.error("[%s] %s %s failed: %s", exchange.upper(), operation.upper(), symbol, e)
                decision["error_reason"] = str(e)
                save_ai_decision(db, account, decision, portfolio, executed=False)

    finally:
        # [2026-08-29] fresh 客户端用完即关（防 aiohttp 会话泄漏/挂死）
        _close_ccxt_client(client)
        db.close()
