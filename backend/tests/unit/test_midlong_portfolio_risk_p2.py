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


def test_net_exposure_blocks(monkeypatch):
    monkeypatch.setenv("MIDLONG_MAX_NET_EXPOSURE_PCT", "0.30")
    from backend.services.mlto import midlong_portfolio_risk as mpr
    # settings 可能已缓存；强制走 max_net_pct 入参
    positions = [
        {"symbol": "BTC", "side": "long", "size": 0.5, "entry_price": 100000,
         "mark_price": 100000, "trade_nature": "trend_follow", "timeframe_tier": "long"},
    ]
    # notional=50k on equity=100k = 50% already; adding more long should fail at 30%
    portfolio = {"balance": {"total_equity": 100000}, "positions": positions}
    ok, why = mpr.check_portfolio_open_allowed(
        symbol="XPL", action="buy", portfolio=portfolio, new_notional=1000,
        max_net_pct=0.30,
    )
    assert not ok, why
    assert "net_exposure" in why
    assert "before=" in why and "est=$" in why


def test_net_exposure_allows_under_raised_cap():
    """ETH 已占 ~80% 时，默认 1.5 帽下对冲/加仓估计仍可放行。"""
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [
        {"symbol": "ETH", "side": "short", "size": 0.187, "entry_price": 1872.0,
         "mark_price": 1872.0, "trade_nature": "swing", "timeframe_tier": "mid"},
    ]
    # notional ≈ 350 on equity 440 ≈ 80%
    portfolio = {"balance": {"total_equity": 440}, "positions": positions}
    ok, why = check_portfolio_open_allowed(
        symbol="BTC", action="buy", portfolio=portfolio, new_notional=220.0,
        max_net_pct=1.5,
    )
    assert ok, why


def test_estimate_open_notional_matches_fill_scale():
    from backend.services.mlto.midlong_portfolio_risk import estimate_open_notional

    # MLTO margin 7.5% × 10x × $440 ≈ $330（贴近 ETH 实盘 $351）
    est = estimate_open_notional(equity=440, margin_frac=0.075, leverage=10)
    assert 300 <= est <= 360
    # 旧风险公式在同输入下会小一个数量级——这里用 legacy mf=1 走风险路径
    legacy = estimate_open_notional(
        equity=440, margin_frac=1.0, leverage=10, sl_pct=0.036, risk_pct=0.01,
    )
    assert legacy < 200  # 440*0.01/0.036 ≈ 122


def test_nibble_probe_uses_wider_cap(monkeypatch):
    from backend.config import settings as cfg
    monkeypatch.setattr(cfg, "MIDLONG_MAX_NET_EXPOSURE_PCT", 1.0, raising=False)
    monkeypatch.setattr(cfg, "MIDLONG_NIBBLE_NET_EXPOSURE_PCT", 2.0, raising=False)
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    positions = [
        {"symbol": "ETH", "side": "short", "size": 0.187, "entry_price": 1872.0,
         "mark_price": 1872.0, "trade_nature": "swing", "timeframe_tier": "mid"},
    ]
    portfolio = {"balance": {"total_equity": 440}, "positions": positions}
    # after sell: ~80% + 50% = 130% — 普通帽 100% 拒，探针帽 200% 放行
    ok_normal, _ = check_portfolio_open_allowed(
        symbol="BTC", action="sell", portfolio=portfolio, new_notional=220.0,
        is_probe=False,
    )
    assert not ok_normal
    ok_probe, why = check_portfolio_open_allowed(
        symbol="BTC", action="sell", portfolio=portfolio, new_notional=220.0,
        is_probe=True,
    )
    assert ok_probe, why


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
