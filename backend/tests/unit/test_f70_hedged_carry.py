# -*- coding: utf-8 -*-
"""[F70] 对冲 carry 单测：现货纸面通道、对冲腿现金流分解、回测口径。

纯函数部分不联网不读库；DB 部分不可用时自动跳过。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.services.carry import hedged_backtest as hb  # noqa: E402
from backend.services.carry import hedged_carry as hc  # noqa: E402


class TestSpotPaperChannel:
    def test_buy_then_sell_cash_flow(self):
        ch = hc.SpotPaperChannel(taker_bp=10.0, slippage_bp=0.0,
                                 initial_cash_usd=10_000.0)
        ch.buy("BTC", 0.1, 50_000.0)
        # 买入：付 0.1×50000 = 5000 + 手续费 10bp = 5 → 现金 4995
        assert ch.cash_usd == pytest.approx(10_000.0 - 5000.0 - 5.0)
        assert ch.positions["BTC"] == pytest.approx(0.1)
        assert ch.fee_paid_usd == pytest.approx(5.0)
        ch.sell("BTC", 0.1, 51_000.0)
        # 卖出：收 5100 − 手续费 5.1
        assert ch.positions["BTC"] == pytest.approx(0.0, abs=1e-12)
        assert ch.cash_usd == pytest.approx(10_000.0 - 5000.0 - 5.0 + 5100.0 - 5.1)

    def test_slippage_hurts_both_sides(self):
        ch = hc.SpotPaperChannel(taker_bp=0.0, slippage_bp=10.0)
        f_buy = ch.buy("ETH", 1.0, 2_000.0)
        assert f_buy.px > 2_000.0            # 买更贵
        f_sell = ch.sell("ETH", 1.0, 2_000.0)
        assert f_sell.px < 2_000.0           # 卖更便宜
        assert ch.slippage_paid_usd == pytest.approx(2.0 + 2.0, abs=1e-6)

    def test_maker_fee_applied(self):
        ch = hc.SpotPaperChannel(taker_bp=10.0, maker_bp=2.0, slippage_bp=0.0)
        f = ch.buy("BTC", 1.0, 100.0, is_maker=True)
        assert f.fee_usd == pytest.approx(0.02)
        f2 = ch.buy("BTC", 1.0, 100.0)
        assert f2.fee_usd == pytest.approx(0.10)

    def test_equity_with_marks(self):
        ch = hc.SpotPaperChannel(taker_bp=0.0, slippage_bp=0.0,
                                 initial_cash_usd=1_000.0)
        ch.buy("BTC", 0.01, 50_000.0)      # 花 500
        assert ch.equity_usd({"BTC": 50_000.0}) == pytest.approx(1_000.0, abs=1e-6)
        assert ch.equity_usd({"BTC": 60_000.0}) == pytest.approx(1_100.0, abs=1e-6)


class TestHedgedCarrySim:
    def _sim(self, **kw):
        base = dict(
            symbol="BTC", venue="asterdex", notional_usd=1_000.0, qty=0.01,
            entry_spot_px=100_000.0, entry_perp_px=100_100.0, entry_ts=0.0,
            spot_taker_bp=0.0, perp_taker_bp=0.0,
            spot_slippage_bp=0.0, perp_slippage_bp=0.0,
        )
        base.update(kw)
        return hc.HedgedCarrySim(**base)

    def test_basis_convergence_is_profit(self):
        s = self._sim()
        s.enter()
        s.close(100_000.0, 100_000.0, 3600.0)   # 基差 10bp → 0
        # 现货不动；永续空头赚 100bp × 0.01 × 100 = 1 美元
        assert s.perp_pnl_usd == pytest.approx(1.0)
        assert s.spot_pnl_usd == pytest.approx(0.0)
        assert s.net_usd == pytest.approx(1.0)
        assert s.basis_entry_bp == pytest.approx(10.0)
        assert s.basis_exit_bp == pytest.approx(0.0)

    def test_price_move_is_neutral_when_hedged(self):
        s = self._sim()
        s.enter()
        s.close(110_000.0, 110_110.0, 3600.0)   # 两腿同涨 10%，基差比例不变
        assert s.spot_pnl_usd == pytest.approx(100.0)
        assert s.perp_pnl_usd == pytest.approx(-100.1)
        # 名义随价格放大导致极小的残差（qty 在开仓时固定）
        assert abs(s.net_bp) < 1.5

    def test_funding_accrual_sign(self):
        s = self._sim()
        s.enter()
        s.accrue_funding(100_100.0, 0.0001)    # 正费率 → 空头收取
        assert s.funding_usd == pytest.approx(0.01 * 100_100.0 * 0.0001)
        s.accrue_funding(100_100.0, -0.00005)  # 负费率 → 空头支付
        assert s.funding_usd < 0.01 * 100_100.0 * 0.0001
        assert s.funding_periods == 2

    def test_fees_and_slippage_reduce_net(self):
        free = self._sim()
        free.enter(); free.close(100_000.0, 100_100.0, 1.0)
        costly = self._sim(spot_taker_bp=10.0, perp_taker_bp=4.0,
                           spot_slippage_bp=1.0, perp_slippage_bp=0.5)
        # 出场价与入场价相同 → 只留成本，没有基差收敛收益
        costly.enter(); costly.close(100_000.0, 100_100.0, 1.0)
        assert costly.net_usd < free.net_usd
        # 成本 = 现货往返 20bp + 永续往返 8bp + 滑点 3bp ≈ 31bp
        assert costly.net_bp == pytest.approx(-31.0, abs=0.5)

    def test_to_dict_shape(self):
        s = self._sim()
        s.enter(); s.close(100_000.0, 100_000.0, 86_400.0)
        d = s.to_dict()
        assert set(d) >= {"symbol", "net_bp", "net_usd", "funding_usd",
                          "spot_pnl_usd", "perp_pnl_usd", "spot_fee_usd",
                          "perp_fee_usd", "slippage_usd", "hold_hours",
                          "basis_entry_bp", "basis_exit_bp"}
        assert d["hold_hours"] == pytest.approx(24.0)


class TestSimulatePosition:
    def test_end_to_end_numbers(self):
        sim = hc.simulate_position(
            symbol="BTC", venue="asterdex", notional_usd=1_000.0,
            spot_px0=100_000.0, perp_px0=100_000.0,
            spot_px1=100_000.0, perp_px1=100_000.0,
            ts0=0.0, ts1=86_400.0,
            funding_events=[(100_000.0, 0.0001)] * 3,
            spot_taker_bp=0.0, perp_taker_bp=0.0,
            spot_slippage_bp=0.0, perp_slippage_bp=0.0,
        )
        # 3 期 × 0.01 × 100000 × 0.0001 = 0.3 美元
        assert sim.funding_usd == pytest.approx(0.3)
        assert sim.net_usd == pytest.approx(0.3)
        assert sim.funding_periods == 3

    def test_invalid_price_raises(self):
        with pytest.raises(ValueError):
            hc.simulate_position(symbol="BTC", venue="x", spot_px0=0.0,
                                 perp_px0=1.0, spot_px1=1.0, perp_px1=1.0,
                                 ts0=0.0, ts1=1.0)


class TestFundingEventAlignment:
    def test_events_snap_to_period_boundaries(self):
        """8h 结算：事件时间必须落在 8h 边界上。"""
        events = hb.load_funding_events(
            "BTC", venue="asterdex",
            ts_start_ms=1_788_000_000_000, ts_end_ms=1_788_000_000_000 + 86_400_000,
        )
        if not events:
            pytest.skip("无资金费数据")
        step = 8 * 3600 * 1000
        for ts, rate in events:
            assert ts % step == 0, (ts, step)
            assert isinstance(rate, float)


class TestBacktestSymbol:
    def test_shape_and_folds(self):
        res = hb.backtest_symbol("BTC", venue="binance", days=40.0,
                                 hold_days=7.0, step_days=1.0)
        if res.get("error"):
            pytest.skip(res["error"])
        assert res["n_positions"] > 0
        assert set(res) >= {"net_bp_mean", "net_bp_median", "t", "win_ratio",
                            "funding_usd_mean", "basis_convergence_bp_mean",
                            "folds", "window_days"}
        assert len(res["folds"]) == 4
        assert all(f["n"] > 0 for f in res["folds"])

    def test_longer_hold_earns_more_funding(self):
        """持有期越长，累计资金费越多（同为正值费率区间）。"""
        short = hb.backtest_symbol("BTC", venue="binance", days=40.0,
                                   hold_days=7.0, step_days=1.0)
        long_ = hb.backtest_symbol("BTC", venue="binance", days=40.0,
                                   hold_days=30.0, step_days=1.0)
        if short.get("error") or long_.get("error"):
            pytest.skip("数据不足")
        assert long_["funding_usd_mean"] > short["funding_usd_mean"]


class TestBacktestAggregate:
    def test_combo_shape(self):
        res = hb.backtest(["BTC", "ETH"], venue="binance", days=40.0,
                          hold_days=14.0, step_days=1.0)
        assert set(res) >= {"combo_net_bp", "positive_symbols", "n_symbols",
                            "symbols", "venue", "hold_days"}
        assert res["n_symbols"] >= 1

    def test_report_renders(self):
        res = hb.backtest(["BTC"], venue="binance", days=40.0, hold_days=14.0)
        text = hb.format_report(res)
        assert "对冲 carry" in text and "BTC" in text and "组合等权净期望" in text
