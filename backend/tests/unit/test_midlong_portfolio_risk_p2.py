"""P2 midlong_portfolio_risk 冒烟测试。"""
from __future__ import annotations

import time


def _pos(symbol, side="long", tier="long", nature="trend_follow", size=1.0, px=100.0):
    return {"symbol": symbol, "side": side, "size": size, "entry_price": px,
            "mark_price": px, "trade_nature": nature, "timeframe_tier": tier}


def test_corr_cluster_cap_is_lane_specific():
    """[调研轮16 2026-09-16 更新] 簇帽**车道化**后的正确语义。

    原断言（"第三次同向必被拒"）在 `long_lane=True` + `.env`
    `MIDLONG_CORR_CLUSTER_MAX_LONG=3` 下已不成立 —— 这是《垃圾收拾_验收扫描报告_第二轮》
    N2 的**有意决定**：E1 核心宇宙就是 BTC/ETH/SOL，旧的全量 cap=2 让 SOL 在
    BTC/ETH 持仓时永远开不出来。长车道放行 3 个，中线仍守 2 个（9/9 山寨齐跌实证）。
    """
    from backend.services.mlto.midlong_portfolio_risk import check_portfolio_open_allowed

    two_long = [_pos("BTC"), _pos("ETH")]

    # ── 长车道：cap=3 ⇒ 第三个簇内同向仓允许（N2 决策）──
    ok, why = check_portfolio_open_allowed(
        symbol="SOL", action="buy",
        portfolio={"balance": {"total_equity": 100000}, "positions": two_long},
        new_notional=5000, long_lane=True,
    )
    assert ok, f"长车道 cap=3 时第三个簇内同向仓应放行，实际被拒: {why}"

    # ── 长车道已满 3 个 ⇒ 第 4 个被簇帽拒绝 ──
    three_long = [_pos("BTC"), _pos("ETH"), _pos("SOL")]
    ok2, why2 = check_portfolio_open_allowed(
        symbol="SOL", action="buy",
        portfolio={"balance": {"total_equity": 100000}, "positions": three_long},
        new_notional=5000, long_lane=True,
    )
    assert not ok2 and "corr_cluster" in why2, why2

    # ── 中线：cap=2 ⇒ 第三个簇内同向仓仍被拒（原意图保留）──
    two_mid = [_pos("BTC", tier="mid", nature="swing"),
               _pos("ETH", tier="mid", nature="swing")]
    ok3, why3 = check_portfolio_open_allowed(
        symbol="SOL", action="buy",
        portfolio={"balance": {"total_equity": 100000}, "positions": two_mid},
        new_notional=5000, long_lane=False,
    )
    assert not ok3 and "corr_cluster" in why3, why3


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
        "mark_price": 99.0,  # 浮亏：cur_R < 0（浮盈时不再触发 no_progress）
        "peak_pnl_pct": 0.01,  # 价格 1% 峰值 → 0.2R < 0.5R
        "opened_at": opened,
    }
    d = evaluate_no_progress_exit(pos)
    assert d.action == "close", (d.action, d.reason, d.peak_r, d.hold_hours)
    assert "no_progress" in d.reason


def test_no_progress_skips_when_still_green():
    """浮盈中线不应被 no_progress 砍掉 —— 只是还没走到 0.5R。"""
    from backend.services.mlto.midlong_portfolio_risk import evaluate_no_progress_exit

    opened = time.time() - 40 * 3600
    pos = {
        "symbol": "BNB",
        "side": "long",
        "trade_nature": "swing",
        "timeframe_tier": "mid",
        "entry_price": 100.0,
        "sl_price": 95.0,
        "mark_price": 101.0,  # 浮盈
        "peak_pnl_pct": 0.015,  # 0.3R < 0.5R
        "opened_at": opened,
    }
    d = evaluate_no_progress_exit(pos)
    assert d.action == "hold", (d.action, d.reason, d.peak_r)


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
