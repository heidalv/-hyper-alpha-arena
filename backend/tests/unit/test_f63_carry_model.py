# -*- coding: utf-8 -*-
"""[F63] carry 经济模型单测：盈亏平衡期数、净收益、裁决 fail-closed、统计量。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.services.carry import carry_backtest as bt  # noqa: E402
from backend.services.carry import funding_model as fm  # noqa: E402


class TestCosts:
    def test_round_trip_cost_default(self):
        # 永续 4bp×2 + 现货 5bp×2 = 18bp
        assert fm.round_trip_cost_bp() == pytest.approx(18.0)

    def test_round_trip_cost_custom(self):
        assert fm.round_trip_cost_bp(perp_taker_bp=0.0, spot_taker_bp=0.0) == 0.0
        assert fm.round_trip_cost_bp(perp_taker_bp=4.0, spot_taker_bp=0.0) == 8.0

    def test_negative_inputs_clamped(self):
        assert fm.round_trip_cost_bp(perp_taker_bp=-5.0, spot_taker_bp=-1.0) == 0.0


class TestBreakeven:
    def test_none_when_funding_non_positive(self):
        assert fm.breakeven_periods(0.0, 8.0) is None
        assert fm.breakeven_periods(-0.5, 8.0) is None

    def test_zero_cost_means_zero_periods(self):
        assert fm.breakeven_periods(1.0, 0.0) == 0.0

    def test_typical_case(self):
        # 单期 0.5bp，成本 18bp → 36 期 = 12 天
        be = fm.breakeven_periods(0.5, 18.0)
        assert be == pytest.approx(36.0)
        assert be / fm.PERIODS_PER_DAY == pytest.approx(12.0)


class TestNetCarry:
    def test_net_positive_after_enough_periods(self):
        # 0.5bp × 90 期(30 天) − 18bp = 27bp
        assert fm.net_carry_bp(avg_funding_bp=0.5, periods=90, cost_bp=18.0) == pytest.approx(27.0)

    def test_basis_drift_subtracted(self):
        assert fm.net_carry_bp(avg_funding_bp=1.0, periods=10, cost_bp=8.0,
                               basis_drift_bp=3.0) == pytest.approx(-1.0)

    def test_annualized(self):
        # 0.5bp/期 × 1095 期/年 = 547.5bp = 5.475%
        assert fm.annualized_pct(0.5) == pytest.approx(5.475)


class TestVenuePeriods:
    """周期口径必须按场地区分——实测 hyperliquid 是 1h 结算，其余 8h。"""

    def test_hyperliquid_is_hourly(self):
        assert fm.period_hours("hyperliquid") == 1.0
        assert fm.periods_per_day("hyperliquid") == 24.0
        assert fm.periods_per_year("hyperliquid") == pytest.approx(8760.0)

    def test_major_venues_are_8h(self):
        for v in ("asterdex", "binance", "bybit", "okx", "gateio"):
            assert fm.period_hours(v) == 8.0, v
            assert fm.periods_per_day(v) == 3.0

    def test_unknown_venue_defaults_8h(self):
        assert fm.period_hours("nope") == fm.DEFAULT_PERIOD_HOURS

    def test_metrics_respect_venue(self):
        m = fm.carry_metrics(avg_funding_bp=0.1, cost_bp=0.0, hold_days=30.0,
                             venue="hyperliquid")
        assert m["periods"] == pytest.approx(720.0)
        assert m["net_bp"] == pytest.approx(72.0)
        assert m["annualized_pct"] == pytest.approx(0.1 * 8760 / 100.0)


class TestCarryMetrics:
    def test_30d_metrics(self):
        m = fm.carry_metrics(avg_funding_bp=1.0, cost_bp=18.0, hold_days=30.0)
        assert m["periods"] == pytest.approx(90.0)
        assert m["net_bp"] == pytest.approx(72.0)
        assert m["net_usd_per_1k"] == pytest.approx(7.2)
        assert m["breakeven_days"] == pytest.approx(6.0)
        assert m["profitable"] is True

    def test_short_hold_is_unprofitable(self):
        m = fm.carry_metrics(avg_funding_bp=1.0, cost_bp=18.0, hold_days=3.0)
        assert m["net_bp"] < 0 and m["profitable"] is False


class TestDecideCarry:
    def test_fail_closed_on_missing_funding(self):
        d = fm.decide_carry(avg_funding_bp=None, cost_bp=18.0, planned_hold_days=30.0)
        assert d["executable"] is False
        assert "未验证" in d["reason"]

    def test_fail_closed_on_negative_funding(self):
        d = fm.decide_carry(avg_funding_bp=-0.2, cost_bp=18.0, planned_hold_days=30.0)
        assert d["executable"] is False and "≤ 0" in d["reason"]

    def test_fail_closed_when_hold_below_breakeven(self):
        d = fm.decide_carry(avg_funding_bp=1.0, cost_bp=18.0, planned_hold_days=3.0)
        assert d["executable"] is False and "需持有" in d["reason"]

    def test_executable_when_everything_ok(self):
        d = fm.decide_carry(avg_funding_bp=1.0, cost_bp=18.0, planned_hold_days=30.0)
        assert d["executable"] is True and d["reason"] == ""
        assert d["net_bp"] == pytest.approx(72.0)

    def test_min_hold_guard(self):
        d = fm.decide_carry(avg_funding_bp=5.0, cost_bp=18.0, planned_hold_days=10.0,
                            min_hold_days=14.0)
        assert d["executable"] is False and "下限" in d["reason"]

    def test_min_net_guard(self):
        d = fm.decide_carry(avg_funding_bp=1.0, cost_bp=18.0, planned_hold_days=30.0,
                            min_net_bp=100.0)
        assert d["executable"] is False and "阈值" in d["reason"]


class TestFundingStats:
    def test_empty(self):
        st = fm.funding_stats([])
        assert st["n"] == 0 and st["mean_bp"] == 0.0 and st["t"] == 0.0

    def test_basic(self):
        st = fm.funding_stats([1.0, 1.0, 1.0, 1.0])
        assert st["n"] == 4
        assert st["mean_bp"] == pytest.approx(1.0)
        assert st["positive_ratio"] == 1.0
        assert st["t"] == 0.0          # 零方差 → t 定义为 0（不虚报显著性）

    def test_mixed_signs(self):
        st = fm.funding_stats([1.0, -1.0, 1.0, -1.0])
        assert st["mean_bp"] == pytest.approx(0.0)
        assert st["positive_ratio"] == pytest.approx(0.5)
        assert st["min_bp"] == -1.0 and st["max_bp"] == 1.0


class TestBacktestSymbol:
    def test_folds_and_holds(self):
        rates = [0.5] * 80          # 80 期 0.5bp
        r = bt.backtest_symbol("BTC", rates, cost_bp=18.0)
        assert r["stats"]["n"] == 80
        assert r["holds"]["30d"]["net_bp"] == pytest.approx(0.5 * 90 - 18.0)
        assert r["breakeven_days"] == pytest.approx(12.0)
        assert len(r["folds"]) == 4
        assert all(f["n"] == 20 for f in r["folds"])

    def test_short_series_no_folds(self):
        r = bt.backtest_symbol("ETH", [0.1] * 10, cost_bp=18.0)
        assert r["folds"] == []

    def test_negative_funding_unprofitable(self):
        r = bt.backtest_symbol("XRP", [-0.5] * 100, cost_bp=18.0)
        assert r["breakeven_days"] is None
        assert all(not h["profitable"] for h in r["holds"].values())


class TestFormatReport:
    def test_report_renders(self):
        res = {
            "days": 90.0, "cost_bp": 18.0,
            "cost_breakdown": {"perp_round_trip_bp": 8.0, "spot_round_trip_bp": 10.0},
            "venues": {"binance": {
                "symbols": {"BTC": bt.backtest_symbol("BTC", [0.5] * 80, cost_bp=18.0)},
                "combo_30d": fm.carry_metrics(avg_funding_bp=0.5, cost_bp=18.0, hold_days=30.0),
                "executable_symbols": [],
            }},
        }
        text = bt.format_report(res)
        assert "binance" in text and "BTC" in text and "组合(30d)" in text
