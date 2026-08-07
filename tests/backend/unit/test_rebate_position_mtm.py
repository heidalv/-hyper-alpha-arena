"""Paper 仓位 Mark-to-Market 单元测试"""

import time
from unittest.mock import patch

from backend.services.rebate_arb.models import RebatePosition, RebatePositionStatus, RebateStrategyType
from backend.services.rebate_arb.rebate_paper_simulator import PaperLegFill, calc_unrealized_leg_pnl
from backend.services.rebate_arb.rebate_position_mtm import (
    refresh_position_mtm,
    serialize_position_for_api,
)


def test_calc_unrealized_leg_pnl_long_profit():
    entry = PaperLegFill(
        exchange="asterdex",
        side="buy",
        order_type="market",
        size_usd=45.0,
        ref_price=100.0,
        filled_price=100.0,
        size_coins=0.45,
        slippage_rate=0.0,
        slippage_cost_usd=0.0,
        fee_rate=0.00005,
        fee_paid=0.00225,
        rebate_rate=0.1,
        rebate_received=0.000225,
        is_maker=False,
    )
    u = calc_unrealized_leg_pnl(entry, mark_price=110.0)
    assert u["gross_pnl"] == 4.5
    assert round(u["net_pnl"], 4) == round(4.5 - 0.00225 + 0.000225, 4)


def test_calc_unrealized_leg_pnl_short_loss():
    entry = PaperLegFill(
        exchange="asterdex",
        side="sell",
        order_type="market",
        size_usd=45.0,
        ref_price=600.0,
        filled_price=600.0,
        size_coins=0.075,
        slippage_rate=0.0,
        slippage_cost_usd=0.0,
        fee_rate=0.00005,
        fee_paid=0.00225,
        rebate_rate=0.1,
        rebate_received=0.000225,
        is_maker=False,
    )
    u = calc_unrealized_leg_pnl(entry, mark_price=610.0)
    assert u["gross_pnl"] == -0.75


def test_refresh_position_mtm_updates_pnl_and_metadata():
    pos = RebatePosition(
        position_id="test-pos-1",
        strategy_type=RebateStrategyType.S8_ASTERDEX_RH,
        source_exchange="asterdex",
        target_exchange=None,
        symbol="SOL/USDT",
        side_a_size=45.0,
        entry_price_a=150.0,
        entry_time=time.time() - 3600,
        paper_mode=True,
        status=RebatePositionStatus.ACTIVE,
        metadata={
            "paper_entry_fills": {
                "side_a": {
                    "exchange": "asterdex",
                    "side": "buy",
                    "order_type": "market",
                    "size_usd": 45.0,
                    "ref_price": 150.0,
                    "filled_price": 150.0,
                    "size_coins": 0.3,
                    "slippage_rate": 0.0,
                    "slippage_cost_usd": 0.0,
                    "fee_rate": 0.00005,
                    "fee_paid": 0.00225,
                    "rebate_rate": 0.1,
                    "rebate_received": 0.000225,
                    "is_maker": False,
                    "is_close": False,
                }
            }
        },
    )

    with patch(
        "backend.services.rebate_arb.rebate_position_mtm.resolve_paper_market",
    ) as mock_market:
        from backend.services.rebate_arb.rebate_paper_market import PaperMarketQuote

        mock_market.return_value = PaperMarketQuote(
            symbol="SOL/USDT",
            exchange="asterdex",
            mid=155.0,
            bid=154.95,
            ask=155.05,
            mark=155.0,
            spread_bps=6.5,
            funding_rate=0.0001,
            source="test",
            price_exchange="binance",
            ts=time.time(),
        )
        ok = refresh_position_mtm(pos)

    assert ok is True
    assert pos.current_pnl != 0.0
    assert pos.metadata["mark_price"] == 155.0
    assert pos.metadata["entry_price"] == 150.0
    assert pos.accumulated_points >= 0

    api = serialize_position_for_api(pos)
    assert api["entry_price"] == 150.0
    assert api["mark_price"] == 155.0
    assert api["side"] == "buy"
    assert api["hold_duration_hours"] >= 0.9
