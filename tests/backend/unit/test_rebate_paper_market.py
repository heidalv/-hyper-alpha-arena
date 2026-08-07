"""Paper 真实行情与成交模拟测试"""

import time

from backend.services.rebate_arb.rebate_paper_market import (
    PaperMarketQuote,
    calc_funding_pnl,
    pick_reference_price,
    walk_orderbook_fill,
)
from backend.services.rebate_arb.rebate_paper_simulator import simulate_leg_fill


def test_pick_reference_price_taker_buy_uses_ask():
    q = PaperMarketQuote(
        symbol="SOL/USDT", exchange="asterdex", mid=100.0, bid=99.9, ask=100.1,
        mark=100.0, spread_bps=20, funding_rate=0.0, source="t", price_exchange="binance", ts=time.time(),
    )
    px, kind = pick_reference_price(q, "buy", "market")
    assert kind == "ask"
    assert px == 100.1


def test_walk_orderbook_fill_depth():
    asks = [[100.0, 1.0], [100.5, 2.0]]
    avg, qty = walk_orderbook_fill("buy", 150.0, [], asks, 100.0)
    assert avg > 100.0
    assert qty > 0


def test_simulate_leg_fill_with_market_quote():
    q = PaperMarketQuote(
        symbol="BNB/USDT", exchange="asterdex", mid=600.0, bid=599.7, ask=600.3,
        mark=600.0, spread_bps=10, funding_rate=0.00005, source="test", price_exchange="binance", ts=time.time(),
        asks=[[600.3, 10.0]],
        bids=[[599.7, 10.0]],
    )
    fill = simulate_leg_fill(
        exchange="asterdex",
        side="buy",
        order_type="market",
        size_usd=45.0,
        market=q,
        symbol="BNB/USDT",
    )
    assert fill is not None
    assert fill.filled_price >= 600.3
    assert fill.fee_paid > 0
    assert fill.price_source == "test"


def test_calc_funding_pnl_long_pays_positive_rate():
    pnl = calc_funding_pnl("buy", 1000.0, 0.0001, 8.0)
    assert pnl < 0
