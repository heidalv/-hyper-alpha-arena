"""Paper exchange execution simulator.

This module models the exchange-facing part of a trade:

- order trigger / resting behavior
- fill price from bid/ask or a reference mark
- quantity, notional, margin and fee calculation
- exchange-specific fee and minimum order rules

Business services should decide *what* to trade. This simulator decides how a
paper exchange would accept, trigger and fill that order.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Dict, Optional

from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType


class PaperOrderStatus(str, Enum):
    OPEN = "open"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class PaperTriggerReason(str, Enum):
    MARKET = "market"
    MARKETABLE_LIMIT = "marketable_limit"
    RESTING_LIMIT = "resting_limit"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    NONE = "none"


@dataclass(frozen=True)
class PaperExchangeRules:
    exchange: str
    maker_fee_rate: float
    taker_fee_rate: float
    min_notional_usd: float = 10.0
    min_quantity: float = 0.0
    quantity_step: float = 0.0
    price_tick: float = 0.0
    maintenance_margin_rate: float = 0.005


@dataclass(frozen=True)
class PaperMarketState:
    symbol: str
    mark_price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    funding_rate: float = 0.0

    def best_bid(self) -> float:
        return float(self.bid or self.mark_price or 0.0)

    def best_ask(self) -> float:
        return float(self.ask or self.mark_price or 0.0)


@dataclass
class PaperOrderFill:
    status: PaperOrderStatus
    trigger_reason: PaperTriggerReason
    exchange: str
    symbol: str
    side: str
    order_type: str
    requested_quantity: float
    filled_quantity: float = 0.0
    fill_price: float = 0.0
    notional_usd: float = 0.0
    leverage: float = 1.0
    margin_usd: float = 0.0
    fee_rate: float = 0.0
    fee_usd: float = 0.0
    maker: bool = False
    reduce_only: bool = False
    reject_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["status"] = self.status.value
        data["trigger_reason"] = self.trigger_reason.value
        return data


DEFAULT_EXCHANGE_RULES: Dict[str, PaperExchangeRules] = {
    "hyperliquid": PaperExchangeRules(
        "hyperliquid",
        maker_fee_rate=0.0002,
        taker_fee_rate=0.00035,
        min_notional_usd=10.0,
        quantity_step=0.0001,
        maintenance_margin_rate=0.005,
    ),
    # [F54 2026-09-09] 费率修正：原值 maker=taker=0.00005（0.5bp）是 **USD1 通用永续**
    # 的 taker 费率，而本系统实际交易的是 **USDT 永续**。官方费率表
    # （https://docs.asterdex.com/trading/perpetuals/fees-and-specs/fees）：
    #   USDT 永续   maker 0%    taker 0.04%
    #   USD1 永续   maker 0%    taker 0.005%
    #   RWA  永续   maker 0%    taker 0.009%
    # 旧值把 Aster 的往返成本低估了 8 倍（实测 DB 费率 0.50bp 与旧配置一致），
    # 使全部模拟盘/回测结论系统性偏乐观（按真实 taker 重算，历史短线亏损应为
    # ≈ −$528 而非账面 −$202，见《短线根因诊断与处置建议_20260909》）。
    # 允许用环境变量覆盖，便于官方调价时无需改代码：
    #   ASTERDEX_MAKER_FEE_BP / ASTERDEX_TAKER_FEE_BP
    "asterdex": PaperExchangeRules(
        "asterdex",
        maker_fee_rate=float(os.getenv("ASTERDEX_MAKER_FEE_BP", "0")) / 10000.0,
        taker_fee_rate=float(os.getenv("ASTERDEX_TAKER_FEE_BP", "4")) / 10000.0,
        min_notional_usd=5.0,
        quantity_step=0.0001,
        maintenance_margin_rate=0.005,
    ),
    "binance": PaperExchangeRules(
        "binance",
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0004,
        min_notional_usd=5.0,
        quantity_step=0.0001,
        maintenance_margin_rate=0.004,
    ),
    "okx": PaperExchangeRules(
        "okx",
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
        min_notional_usd=5.0,
        quantity_step=0.0001,
        maintenance_margin_rate=0.005,
    ),
    "bybit": PaperExchangeRules(
        "bybit",
        maker_fee_rate=0.0002,
        taker_fee_rate=0.00055,
        min_notional_usd=5.0,
        quantity_step=0.0001,
        maintenance_margin_rate=0.005,
    ),
    "gateio": PaperExchangeRules(
        "gateio",
        maker_fee_rate=0.0002,
        taker_fee_rate=0.0005,
        min_notional_usd=10.0,
        quantity_step=0.0001,
        maintenance_margin_rate=0.005,
    ),
}

EXCHANGE_ALIASES: Dict[str, str] = {
    "hl": "hyperliquid",
    "hyper": "hyperliquid",
    "aster": "asterdex",
    "aster_dex": "asterdex",
    "binanceusdm": "binance",
    "binance_usdm": "binance",
    "gate": "gateio",
}


def _fallback_exchange_key() -> str:
    """规则表回退交易所（2026-08-31 起用币安）。

    历史默认：未知→hyperliquid（$10 门槛，已停用该所）、空串→asterdex。
    当前主力交易所为 binance（.env DEFAULT_EXCHANGE=binance），未知/缺失
    交易所一律回退币安规则；PAPER_EXCHANGE_RULES_FALLBACK 可覆盖。
    """
    _v = str(os.getenv("PAPER_EXCHANGE_RULES_FALLBACK", "binance") or "binance").strip().lower()
    return _v if _v in DEFAULT_EXCHANGE_RULES else "binance"


def get_paper_exchange_rules(exchange: str) -> PaperExchangeRules:
    _fb = _fallback_exchange_key()
    key = (exchange or _fb).lower().strip()
    key = EXCHANGE_ALIASES.get(key, key)
    return DEFAULT_EXCHANGE_RULES.get(key, DEFAULT_EXCHANGE_RULES[_fb])


def liquidation_price(entry_price: float, side: str, leverage: float, maintenance_margin_rate: float = 0.005) -> float:
    """Simple isolated perp liquidation estimate."""
    entry = float(entry_price or 0)
    lev = float(leverage or 1)
    if entry <= 0 or lev <= 1:
        return 0.0
    side_l = (side or "").lower()
    if side_l in ("buy", "long"):
        return entry * (1 - (1 / lev) + maintenance_margin_rate)
    return entry * (1 + (1 / lev) - maintenance_margin_rate)


def _round_down_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return int(value / step) * step


def _is_step_aligned(value: float, step: float) -> bool:
    if step <= 0:
        return True
    scaled = float(value) / float(step)
    return abs(scaled - round(scaled)) < 1e-9


def _limit_is_marketable(order: ExchangeOrder, market: PaperMarketState) -> bool:
    if order.price is None or order.price <= 0:
        return False
    if order.side == OrderSide.BUY:
        return market.best_ask() <= float(order.price)
    return market.best_bid() >= float(order.price)


def _fill_price(order: ExchangeOrder, market: PaperMarketState, *, maker: bool) -> float:
    if maker and order.price and order.price > 0:
        return float(order.price)
    if order.side == OrderSide.BUY:
        base = market.best_ask()
    else:
        base = market.best_bid()
    # M6 成本真实化：PAPER_COST_MODEL_ENABLED 时按统一成本模型滑价
    import os as _os
    if _os.getenv("PAPER_COST_MODEL_ENABLED", "false").lower() in ("1", "true", "yes", "on"):
        try:
            from backend.services.backtest_engine.cost_model import CostModel
            cost = CostModel()
            notional = float(order.price or base) * float(order.size or 0)
            slip = cost.calc_slippage_rate(notional, trade_nature="scalp", is_sl=False)
            if order.side == OrderSide.BUY:
                base = base * (1 + cost.taker_fee + slip)
            else:
                base = base * (1 - cost.taker_fee - slip)
        except Exception:
            pass
    return base


def simulate_exchange_order(
    *,
    exchange: str,
    order: ExchangeOrder,
    market: PaperMarketState,
    available_balance: Optional[float] = None,
    rules: Optional[PaperExchangeRules] = None,
    resting_limit: bool = False,
) -> PaperOrderFill:
    """Simulate exchange order acceptance and fill.

    `order.size` is coin quantity, matching `ExchangeOrder`.
    Margin is always derived as: fill_price * quantity / leverage.
    """
    rules = rules or get_paper_exchange_rules(exchange)
    quantity = _round_down_step(max(float(order.size or 0), 0.0), rules.quantity_step)
    side = order.side.value if isinstance(order.side, OrderSide) else str(order.side)
    order_type = order.order_type.value if isinstance(order.order_type, OrderType) else str(order.order_type)
    leverage = max(float(order.leverage or 1), 1.0)

    base = PaperOrderFill(
        status=PaperOrderStatus.REJECTED,
        trigger_reason=PaperTriggerReason.NONE,
        exchange=rules.exchange,
        symbol=order.symbol,
        side=side,
        order_type=order_type,
        requested_quantity=float(order.size or 0),
        leverage=leverage,
        reduce_only=bool(order.reduce_only),
    )

    if quantity <= 0 or quantity < rules.min_quantity:
        base.reject_reason = "quantity_below_minimum"
        return base
    if market.mark_price <= 0:
        base.reject_reason = "missing_market_price"
        return base

    maker = False
    trigger = PaperTriggerReason.MARKET
    if order.order_type == OrderType.LIMIT:
        if order.price is None or float(order.price) <= 0:
            base.reject_reason = "invalid_limit_price"
            return base
        if not _is_step_aligned(float(order.price), rules.price_tick):
            base.reject_reason = "price_tick_violation"
            return base
        if not _limit_is_marketable(order, market):
            base.status = PaperOrderStatus.OPEN
            base.trigger_reason = PaperTriggerReason.RESTING_LIMIT
            return base
        trigger = PaperTriggerReason.MARKETABLE_LIMIT
        # [2026-08-31 修复] 挂单复查路径（resting_limit=True，paper 引擎对
        # 已挂限价单的后续 check_pending_orders 复查）价格穿越挂单价时应按
        # 挂单价 maker 成交——挂单方提供了流动性且保留价格改善。P2-4 的
        # "可市价化限价单=taker" 只适用于【新下】即穿越的限价单。
        if resting_limit and order.price and float(order.price) > 0:
            maker = True
        # [P2-4] 可市价化限价单 = taker：按对侧盘口价成交、收 taker 费。
        # 原 maker = bool(resting_limit) 且 paper 引擎对新限价单恒传 resting_limit=True，
        # 导致所有可市价化限价单被误判 maker（按限价成交 + maker 费）→ 成交价失真、费率低估。
        # resting 挂单的后续成交（check_pending_orders 路径）仍走 resting 分支之前就 return，
        # 不受本分支影响。
        else:
            maker = False

    price = _fill_price(order, market, maker=maker)
    notional = price * quantity
    if notional < rules.min_notional_usd:
        base.reject_reason = "notional_below_minimum"
        base.notional_usd = notional
        return base

    margin = notional / leverage
    fee_rate = rules.maker_fee_rate if maker else rules.taker_fee_rate
    fee = notional * fee_rate
    if available_balance is not None and not bool(order.reduce_only) and margin + fee > float(available_balance):
        base.reject_reason = "insufficient_margin"
        base.notional_usd = notional
        base.margin_usd = margin
        base.fee_rate = fee_rate
        base.fee_usd = fee
        return base

    return PaperOrderFill(
        status=PaperOrderStatus.FILLED,
        trigger_reason=trigger,
        exchange=rules.exchange,
        symbol=order.symbol,
        side=side,
        order_type=order_type,
        requested_quantity=float(order.size or 0),
        filled_quantity=quantity,
        fill_price=price,
        notional_usd=notional,
        leverage=leverage,
        margin_usd=margin,
        fee_rate=fee_rate,
        fee_usd=fee,
        maker=maker,
        reduce_only=bool(order.reduce_only),
    )


def simulate_notional_order(
    *,
    exchange: str,
    symbol: str,
    side: str,
    order_type: str,
    target_notional_usd: float,
    reference_price: float,
    leverage: float = 1.0,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    available_balance: Optional[float] = None,
) -> PaperOrderFill:
    """Create a coin-quantity order from a target notional and simulate it.

    Real perp APIs normally place orders by base coin quantity. The target
    notional is therefore converted to quantity using the reference price first;
    the actual filled notional is then `filled_price * quantity`.
    """
    ref = float(reference_price or 0)
    if ref <= 0:
        return PaperOrderFill(
            status=PaperOrderStatus.REJECTED,
            trigger_reason=PaperTriggerReason.NONE,
            exchange=(exchange or "").lower(),
            symbol=symbol,
            side=side,
            order_type=order_type,
            requested_quantity=0.0,
            leverage=max(float(leverage or 1), 1.0),
            reject_reason="missing_reference_price",
        )
    quantity = max(float(target_notional_usd or 0), 0.0) / ref
    return simulate_exchange_order(
        exchange=exchange,
        order=ExchangeOrder(
            order_id="paper_notional",
            symbol=symbol,
            side=OrderSide.BUY if (side or "buy").lower() == "buy" else OrderSide.SELL,
            order_type=OrderType.LIMIT if (order_type or "market").lower() == "limit" else OrderType.MARKET,
            size=quantity,
            price=reference_price if (order_type or "market").lower() == "limit" else None,
            leverage=int(round(max(float(leverage or 1), 1.0))),
        ),
        market=PaperMarketState(
            symbol=symbol,
            mark_price=ref,
            bid=bid,
            ask=ask,
        ),
        available_balance=available_balance,
    )


def evaluate_attached_tp_sl(
    *,
    position_side: str,
    mark_price: float,
    take_profit: Optional[float] = None,
    stop_loss: Optional[float] = None,
) -> PaperTriggerReason:
    """Evaluate exchange-style attached TP/SL trigger for an open position."""
    side = (position_side or "").lower()
    mark = float(mark_price or 0)
    if mark <= 0:
        return PaperTriggerReason.NONE
    tp = float(take_profit or 0)
    sl = float(stop_loss or 0)
    if side == "long":
        if sl > 0 and mark <= sl:
            return PaperTriggerReason.STOP_LOSS
        if tp > 0 and mark >= tp:
            return PaperTriggerReason.TAKE_PROFIT
    elif side == "short":
        if sl > 0 and mark >= sl:
            return PaperTriggerReason.STOP_LOSS
        if tp > 0 and mark <= tp:
            return PaperTriggerReason.TAKE_PROFIT
    return PaperTriggerReason.NONE
