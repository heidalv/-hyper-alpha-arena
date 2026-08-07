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
    "asterdex": PaperExchangeRules(
        "asterdex",
        maker_fee_rate=0.00005,
        taker_fee_rate=0.00005,
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


def get_paper_exchange_rules(exchange: str) -> PaperExchangeRules:
    key = (exchange or "asterdex").lower().strip()
    key = EXCHANGE_ALIASES.get(key, key)
    return DEFAULT_EXCHANGE_RULES.get(key, DEFAULT_EXCHANGE_RULES["hyperliquid"])


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
        maker = bool(resting_limit)

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
