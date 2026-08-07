"""P2 midlong_portfolio_risk 冒烟测试。"""
from __future__ import annotations

import time


def test_corr_cluster_blocks_third_same_dir():
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [
        {"symbol": "BTC", "side": "long", "size": 0.1, "entry_price": 100000,
         "mark_price": 100000, "trade_nature": "trend_follow", "timeframe_tier": "long"},
        {"symbol": "ETH", "side": "long", "size": 1.0, "entry_price": 3000,
         "mark_price": 3000, "trade_nature": "trend_follow", "timeframe_tier": "long"},
    ]
    portfolio = {"balance": {"total_equity": 100000}, "positions": positions}
    ok, why = check_portfolio_open_allowed(
        symbol="SOL", action="buy", portfolio=portfolio, new_notional=5000,
    )
    assert not ok, why
    assert "corr_cluster" in why


def test_net_exposure_blocks():
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [
        {"symbol": "BTC", "side": "long", "size": 0.5, "entry_price": 100000,
         "mark_price": 100000, "trade_nature": "trend_follow", "timeframe_tier": "long"},
    ]
    # notional=50k on equity=100k = 50% already; adding more long should fail at 30%
    portfolio = {"balance": {"total_equity": 100000}, "positions": positions}
    ok, why = check_portfolio_open_allowed(
        symbol="XPL", action="buy", portfolio=portfolio, new_notional=1000,
    )
    assert not ok, why
    assert "net_exposure" in why


def test_no_progress_triggers():
    from backend.services.mlto.midlong_portfolio_risk import evaluate_no_progress_exit

    opened = time.time() - 80 * 3600  # 80h ago
    pos = {
        "symbol": "BTC",
        "side": "long",
        "trade_nature": "trend_follow",
        "timeframe_tier": "long",
        "entry_price": 100.0,
        "sl_price": 95.0,  # 5% R
        "mark_price": 100.5,
        "peak_pnl_pct": 0.01,  # 价格 1% 峰值 → 0.2R < 0.5R
        "opened_at": opened,
    }
    d = evaluate_no_progress_exit(pos)
    assert d.action == "close", (d.action, d.reason, d.peak_r, d.hold_hours)
    assert "no_progress" in d.reason


def test_no_progress_skips_when_peak_ok():
    from backend.services.mlto.midlong_portfolio_risk import evaluate_no_progress_exit

    opened = time.time() - 80 * 3600
    pos = {
        "symbol": "BTC",
        "side": "long",
        "trade_nature": "trend_follow",
        "timeframe_tier": "long",
        "entry_price": 100.0,
        "sl_price": 95.0,
        "mark_price": 104.0,
        "peak_pnl_pct": 0.04,  # 4%/5% = 0.8R >= 0.5R
        "opened_at": opened,
    }
    d = evaluate_no_progress_exit(pos)
    assert d.action == "hold", (d.action, d.reason, d.peak_r)


def test_core_basket_parse():
    from backend.services.mlto import midlong_portfolio_risk as mpr
    # 不依赖 .env 强制值：函数可调用即可
    basket = mpr.parse_core_basket()
    assert isinstance(basket, list)
