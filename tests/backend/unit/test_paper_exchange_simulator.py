"""Unit tests for paper exchange order simulation."""

from typing import Optional

import pytest

from backend.services.exchange.base_exchange_client import ExchangeOrder, OrderSide, OrderType
from backend.services.exchange.paper_exchange_simulator import (
    PaperMarketState,
    PaperExchangeRules,
    PaperOrderStatus,
    PaperTriggerReason,
    evaluate_attached_tp_sl,
    get_paper_exchange_rules,
    simulate_exchange_order,
    simulate_notional_order,
)


def _order(
    *,
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    size: float = 1.0,
    price: Optional[float] = None,
    leverage: int = 10,
    reduce_only: bool = False,
) -> ExchangeOrder:
    return ExchangeOrder(
        order_id="test_order",
        symbol="BTC",
        side=side,
        order_type=order_type,
        size=size,
        price=price,
        leverage=leverage,
        reduce_only=reduce_only,
    )


def test_market_buy_fills_at_ask_and_derives_margin_from_leverage():
    fill = simulate_exchange_order(
        exchange="hyperliquid",
        order=_order(side=OrderSide.BUY, size=2, leverage=20),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=101),
        available_balance=1_000,
    )

    assert fill.status == PaperOrderStatus.FILLED
    assert fill.trigger_reason == PaperTriggerReason.MARKET
    assert fill.fill_price == 101
    assert fill.notional_usd == 202
    assert fill.margin_usd == 202 / 20


def test_resting_limit_order_stays_open_until_price_is_marketable():
    fill = simulate_exchange_order(
        exchange="hyperliquid",
        order=_order(side=OrderSide.BUY, order_type=OrderType.LIMIT, size=1, price=95),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=101),
        available_balance=1_000,
    )

    assert fill.status == PaperOrderStatus.OPEN
    assert fill.trigger_reason == PaperTriggerReason.RESTING_LIMIT
    assert fill.filled_quantity == 0


def test_marketable_limit_order_fills():
    fill = simulate_exchange_order(
        exchange="hyperliquid",
        order=_order(side=OrderSide.SELL, order_type=OrderType.LIMIT, size=1, price=98),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=101),
        available_balance=1_000,
    )

    assert fill.status == PaperOrderStatus.FILLED
    assert fill.trigger_reason == PaperTriggerReason.MARKETABLE_LIMIT
    assert fill.fill_price == 99


def test_resting_limit_order_fills_as_maker_at_limit_price():
    fill = simulate_exchange_order(
        exchange="hyperliquid",
        order=_order(side=OrderSide.SELL, order_type=OrderType.LIMIT, size=1, price=105),
        market=PaperMarketState(symbol="BTC", mark_price=106, bid=106, ask=107),
        available_balance=1_000,
        resting_limit=True,
    )

    assert fill.status == PaperOrderStatus.FILLED
    assert fill.trigger_reason == PaperTriggerReason.MARKETABLE_LIMIT
    assert fill.maker is True
    assert fill.fill_price == 105
    assert fill.fee_rate == 0.0002


def test_insufficient_margin_rejects_non_reduce_only_order():
    fill = simulate_exchange_order(
        exchange="hyperliquid",
        order=_order(side=OrderSide.BUY, size=10, leverage=1),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=101),
        available_balance=10,
    )

    assert fill.status == PaperOrderStatus.REJECTED
    assert fill.reject_reason == "insufficient_margin"


def test_reduce_only_order_does_not_require_open_margin():
    fill = simulate_exchange_order(
        exchange="hyperliquid",
        order=_order(side=OrderSide.SELL, size=10, leverage=1, reduce_only=True),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=101),
        available_balance=10,
    )

    assert fill.status == PaperOrderStatus.FILLED
    assert fill.reduce_only is True


def test_exchange_fee_rules_are_exchange_specific():
    hyper = simulate_exchange_order(
        exchange="hyperliquid",
        order=_order(side=OrderSide.BUY, size=1, leverage=10),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=100),
        available_balance=1_000,
    )
    aster = simulate_exchange_order(
        exchange="asterdex",
        order=_order(side=OrderSide.BUY, size=1, leverage=10),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=100),
        available_balance=1_000,
    )

    assert hyper.fee_rate == get_paper_exchange_rules("hyperliquid").taker_fee_rate
    assert aster.fee_rate == get_paper_exchange_rules("asterdex").taker_fee_rate
    assert aster.fee_usd < hyper.fee_usd


def test_quantity_step_rounds_down_before_notional_and_margin():
    fill = simulate_exchange_order(
        exchange="custom",
        order=_order(side=OrderSide.BUY, size=1.234, leverage=10),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=100),
        available_balance=1_000,
        rules=PaperExchangeRules(
            "custom",
            maker_fee_rate=0.001,
            taker_fee_rate=0.002,
            min_notional_usd=10,
            quantity_step=0.1,
        ),
    )

    assert fill.status == PaperOrderStatus.FILLED
    assert fill.filled_quantity == pytest.approx(1.2)
    assert fill.notional_usd == pytest.approx(120)
    assert fill.margin_usd == pytest.approx(12)


def test_min_notional_rule_rejects_tiny_orders():
    fill = simulate_exchange_order(
        exchange="custom",
        order=_order(side=OrderSide.BUY, size=0.09, leverage=10),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=100),
        available_balance=1_000,
        rules=PaperExchangeRules(
            "custom",
            maker_fee_rate=0.001,
            taker_fee_rate=0.002,
            min_notional_usd=10,
        ),
    )

    assert fill.status == PaperOrderStatus.REJECTED
    assert fill.reject_reason == "notional_below_minimum"


def test_limit_price_must_match_exchange_price_tick():
    fill = simulate_exchange_order(
        exchange="custom",
        order=_order(side=OrderSide.BUY, order_type=OrderType.LIMIT, size=1, price=100.03),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=100),
        available_balance=1_000,
        rules=PaperExchangeRules(
            "custom",
            maker_fee_rate=0.001,
            taker_fee_rate=0.002,
            min_notional_usd=10,
            price_tick=0.05,
        ),
    )

    assert fill.status == PaperOrderStatus.REJECTED
    assert fill.reject_reason == "price_tick_violation"


def test_limit_price_on_tick_can_fill():
    fill = simulate_exchange_order(
        exchange="custom",
        order=_order(side=OrderSide.BUY, order_type=OrderType.LIMIT, size=1, price=100.05),
        market=PaperMarketState(symbol="BTC", mark_price=100, bid=99, ask=100),
        available_balance=1_000,
        rules=PaperExchangeRules(
            "custom",
            maker_fee_rate=0.001,
            taker_fee_rate=0.002,
            min_notional_usd=10,
            price_tick=0.05,
        ),
    )

    assert fill.status == PaperOrderStatus.FILLED
    assert fill.fill_price == 100


def test_notional_order_converts_to_quantity_before_fill():
    fill = simulate_notional_order(
        exchange="hyperliquid",
        symbol="ETH",
        side="buy",
        order_type="market",
        target_notional_usd=1_000,
        reference_price=100,
        leverage=10,
        ask=101,
        available_balance=1_000,
    )

    assert fill.status == PaperOrderStatus.FILLED
    assert fill.filled_quantity == 10
    assert fill.notional_usd == 1_010
    assert fill.margin_usd == 101


def test_attached_tp_sl_triggers_for_long_and_short_positions():
    assert evaluate_attached_tp_sl(
        position_side="long",
        mark_price=95,
        take_profit=110,
        stop_loss=100,
    ) == PaperTriggerReason.STOP_LOSS
    assert evaluate_attached_tp_sl(
        position_side="long",
        mark_price=111,
        take_profit=110,
        stop_loss=100,
    ) == PaperTriggerReason.TAKE_PROFIT
    assert evaluate_attached_tp_sl(
        position_side="short",
        mark_price=105,
        take_profit=90,
        stop_loss=100,
    ) == PaperTriggerReason.STOP_LOSS
    assert evaluate_attached_tp_sl(
        position_side="short",
        mark_price=89,
        take_profit=90,
        stop_loss=100,
    ) == PaperTriggerReason.TAKE_PROFIT
