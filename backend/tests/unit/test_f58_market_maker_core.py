# -*- coding: utf-8 -*-
"""[F58] 做市核心单测（纯函数/纯状态，无 IO）。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook,
    LaneRiskLimits,
    QuoteParams,
    check_lane_limits,
    compute_quote,
    edge_metric_from_ledger,
    fill_side,
    realized_vol_bp,
    should_stop_loss,
    sigma_norm_from_ranges,
    trend_blocked_side,
    trend_move_bp,
    unrealized_bp,
    vol_regime_blocked,
)


class TestTailGates:
    """[F71] 尾部亏损闸门：止损与趋势侧禁挂。"""

    def test_unrealized_bp_long_and_short(self):
        assert unrealized_bp(1.0, 100.0, 99.7) == pytest.approx(-30.0)
        assert unrealized_bp(-1.0, 100.0, 99.7) == pytest.approx(30.0)
        assert unrealized_bp(0.0, 100.0, 99.7) == 0.0
        assert unrealized_bp(1.0, 0.0, 99.7) == 0.0

    def test_should_stop_loss_threshold(self):
        assert should_stop_loss(1.0, 100.0, 99.7, 25.0) is True    # −30bp
        assert should_stop_loss(1.0, 100.0, 99.8, 25.0) is False   # −20bp
        assert should_stop_loss(-1.0, 100.0, 100.3, 25.0) is True  # 空头 −30bp

    def test_stop_loss_disabled_when_zero(self):
        assert should_stop_loss(1.0, 100.0, 50.0, 0.0) is False
        assert should_stop_loss(1.0, 100.0, 50.0, -1.0) is False

    def test_trend_move_bp(self):
        assert trend_move_bp([100.0] * 19 + [99.5], 20) == pytest.approx(-50.0)
        assert trend_move_bp([100.0] * 19 + [100.5], 20) == pytest.approx(50.0)
        assert trend_move_bp([], 20) == 0.0
        assert trend_move_bp([100.0], 20) == 0.0

    def test_trend_blocked_side_direction(self):
        down = [100.0] * 19 + [99.5]
        up = [100.0] * 19 + [100.5]
        flat = [100.0] * 19 + [100.05]
        assert trend_blocked_side(down, 30.0, 20) == "buy"     # 下跌禁买
        assert trend_blocked_side(up, 30.0, 20) == "sell"      # 上涨禁卖
        assert trend_blocked_side(flat, 30.0, 20) == ""

    def test_trend_gate_disabled_when_zero(self):
        assert trend_blocked_side([100.0] * 19 + [90.0], 0.0, 20) == ""

    def test_limits_defaults_include_gates(self):
        lim = LaneRiskLimits()
        # [2026-09-09 参数扫描] stop_loss_bp 默认 25→0：超时平仓是 taker 腿 +
        # 吃价差，是 -5.75bp/笔 的主要来源；关闭后回放净边际翻正
        # （backend/scripts/sweep_mm_params.py：w5/k1/h300/sl0 → +0.643bp）。
        assert lim.stop_loss_bp == 0
        assert lim.trend_pause_bp >= 0
        assert lim.trend_lookback >= 2


class TestVolRegimeGate:
    """[F71b] 已实现波动闸门：高波动时暂停（平仓成本吞掉价差）。"""

    @staticmethod
    def _series(step: float, n: int = 41):
        # 确定性锯齿：每步交替 ±step，标准差稳定
        out = [100.0]
        for i in range(n):
            out.append(out[-1] * (1.0 + (step if i % 2 == 0 else -step)))
        return out

    def test_realized_vol_scales_with_step(self):
        low = realized_vol_bp(self._series(0.0002))
        high = realized_vol_bp(self._series(0.0008))
        assert high > low * 2.5
        assert low > 0

    def test_flat_series_has_zero_vol(self):
        assert realized_vol_bp([100.0] * 30) == pytest.approx(0.0)

    def test_insufficient_history(self):
        assert realized_vol_bp([]) == 0.0
        assert realized_vol_bp([100.0, 101.0]) == 0.0

    def test_gate_blocks_high_vol_only(self):
        low = self._series(0.0002)
        high = self._series(0.0008)
        base = realized_vol_bp(low)
        blocked_low, _ = vol_regime_blocked(low, base, 1.5)
        blocked_high, cur_high = vol_regime_blocked(high, base, 1.5)
        assert blocked_low is False
        assert blocked_high is True
        assert cur_high > base * 1.5

    def test_gate_disabled_when_mult_zero(self):
        high = self._series(0.001)
        assert vol_regime_blocked(high, 0.1, 0.0)[0] is False

    def test_gate_fail_open_without_baseline(self):
        """基准未知时不拦（避免因缺基线把车道全停）。"""
        assert vol_regime_blocked(self._series(0.001), 0.0, 1.5)[0] is False

    def test_limits_have_vol_fields(self):
        lim = LaneRiskLimits()
        assert hasattr(lim, "vol_pause_mult")
        assert lim.vol_window >= 3


class TestQuote:
    def test_basic_width(self):
        q = compute_quote(symbol="BTC", mid=100.0)
        assert q is not None
        assert q.w_bid_bp == 5.0 and q.w_ask_bp == 5.0
        assert q.bid == pytest.approx(99.95, abs=1e-9)
        assert q.ask == pytest.approx(100.05, abs=1e-9)

    def test_min_width_floor_enforced(self):
        """w_base 低于下限时仍按下限挂（实测 w<=2bp 为负）。"""
        q = compute_quote(symbol="BTC", mid=100.0, params=QuoteParams(w_base_bp=1.0))
        assert q.w_bid_bp >= 3.0 and q.w_ask_bp >= 3.0

    def test_volatility_widens(self):
        q0 = compute_quote(symbol="BTC", mid=100.0)
        q1 = compute_quote(symbol="BTC", mid=100.0, sigma_norm=2.0)
        assert q1.w_bid_bp > q0.w_bid_bp

    def test_inventory_skew_long(self):
        """多头库存 → 买价更远、卖价更近（鼓励减仓）。"""
        q = compute_quote(symbol="BTC", mid=100.0, inv_ratio=1.0)
        assert q.w_bid_bp > q.w_ask_bp

    def test_inventory_skew_short(self):
        q = compute_quote(symbol="BTC", mid=100.0, inv_ratio=-1.0)
        assert q.w_bid_bp < q.w_ask_bp

    def test_max_width_cap(self):
        q = compute_quote(symbol="BTC", mid=100.0, sigma_norm=100.0)
        assert q.w_bid_bp <= 60.0 and q.w_ask_bp <= 60.0

    def test_invalid_mid(self):
        assert compute_quote(symbol="BTC", mid=0) is None
        assert compute_quote(symbol="BTC", mid=-1) is None


class TestSigmaNorm:
    def test_zero_when_baseline_missing(self):
        assert sigma_norm_from_ranges([1, 2, 3], 0) == 0.0

    def test_scales_with_volatility(self):
        assert sigma_norm_from_ranges([2.0, 2.0], 1.0) == pytest.approx(1.0)
        assert sigma_norm_from_ranges([0.5, 0.5], 1.0) == 0.0   # 低于基准 → 夹到 0


class TestInventoryBook:
    def test_open_then_close_realizes_price(self):
        b = InventoryBook()
        b.apply_fill(symbol="BTC", side="buy", qty=1.0, fill_px=99.95, mid_px=100.0, now_ts=1.0)
        assert b.qty("BTC") == 1.0
        r = b.apply_fill(symbol="BTC", side="sell", qty=1.0, fill_px=100.05, mid_px=100.0, now_ts=2.0)
        assert b.qty("BTC") == 0.0
        # 价差捕获：两腿各 +5bp 相对中间价
        assert b.realized_spread_usd == pytest.approx(0.1, abs=1e-6)
        # 中价未动 → 价格盈亏必须为 0（若用成交价对成交均价，会把价差再计一次）
        assert r["price_usd"] == pytest.approx(0.0, abs=1e-12)
        # 总已实现 = 真实买卖差价（99.95 → 100.05）
        assert b.realized_total_usd() == pytest.approx(0.1, abs=1e-6)

    def test_price_pnl_uses_mid_to_mid(self):
        b = InventoryBook()
        b.apply_fill(symbol="ETH", side="buy", qty=1.0, fill_px=99.0, mid_px=100.0, now_ts=1.0)
        r = b.apply_fill(symbol="ETH", side="sell", qty=1.0, fill_px=101.0, mid_px=101.0, now_ts=2.0)
        # 中价 +1% → 价格盈亏 +1.0；卖在中间价上 → 该腿价差为 0
        assert r["price_usd"] == pytest.approx(1.0, abs=1e-9)
        assert r["spread_usd"] == pytest.approx(0.0, abs=1e-9)
        assert b.realized_total_usd() == pytest.approx(2.0, abs=1e-6)

    def test_partial_close(self):
        b = InventoryBook()
        b.apply_fill(symbol="ETH", side="buy", qty=2.0, fill_px=100.0, mid_px=100.0)
        r = b.apply_fill(symbol="ETH", side="sell", qty=1.0, fill_px=101.0, mid_px=101.0)
        assert b.qty("ETH") == 1.0
        assert r["price_usd"] == pytest.approx(1.0, abs=1e-9)

    def test_reverse_position(self):
        b = InventoryBook()
        b.apply_fill(symbol="SOL", side="buy", qty=1.0, fill_px=100.0, mid_px=100.0)
        b.apply_fill(symbol="SOL", side="sell", qty=2.0, fill_px=101.0, mid_px=101.0)
        assert b.qty("SOL") == pytest.approx(-1.0)
        assert b.positions["SOL"].avg_px == pytest.approx(101.0)

    def test_average_price_on_add(self):
        b = InventoryBook()
        b.apply_fill(symbol="BNB", side="buy", qty=1.0, fill_px=100.0, mid_px=100.0)
        b.apply_fill(symbol="BNB", side="buy", qty=1.0, fill_px=102.0, mid_px=102.0)
        assert b.positions["BNB"].avg_px == pytest.approx(101.0)

    def test_fee_accumulates(self):
        b = InventoryBook()
        b.apply_fill(symbol="XRP", side="buy", qty=100.0, fill_px=1.0, mid_px=1.0, fee_rate=0.0004)
        assert b.realized_fee_usd == pytest.approx(-0.04, abs=1e-9)

    def test_inv_ratio_clamped(self):
        b = InventoryBook()
        b.apply_fill(symbol="BTC", side="buy", qty=10.0, fill_px=100.0, mid_px=100.0)
        assert b.inv_ratio("BTC", 100.0, limit_notional=100.0) == 1.0
        assert b.inv_ratio("BTC", 100.0, limit_notional=0) == 0.0

    def test_holding_seconds(self):
        b = InventoryBook()
        b.apply_fill(symbol="BTC", side="buy", qty=1.0, fill_px=100.0, mid_px=100.0, now_ts=1000.0)
        assert b.holding_seconds("BTC", 1300.0) == pytest.approx(300.0)
        b.apply_fill(symbol="BTC", side="sell", qty=1.0, fill_px=100.0, mid_px=100.0, now_ts=1400.0)
        assert b.holding_seconds("BTC", 1500.0) == 0.0


class TestFillSide:
    def test_buy_hit(self):
        assert fill_side(bid=99.95, ask=100.05, seg_low=99.90, seg_high=100.02,
                         seg_taker_sell=1.0, seg_taker_buy=0.0) == "buy"

    def test_sell_hit(self):
        assert fill_side(bid=99.95, ask=100.05, seg_low=99.98, seg_high=100.10,
                         seg_taker_sell=0.0, seg_taker_buy=1.0) == "sell"

    def test_both(self):
        assert fill_side(bid=99.95, ask=100.05, seg_low=99.90, seg_high=100.10,
                         seg_taker_sell=1.0, seg_taker_buy=1.0) == "both"

    def test_no_fill_without_taker_flow(self):
        assert fill_side(bid=99.95, ask=100.05, seg_low=99.90, seg_high=100.10,
                         seg_taker_sell=0.0, seg_taker_buy=0.0) is None

    def test_price_not_through(self):
        assert fill_side(bid=99.95, ask=100.05, seg_low=99.96, seg_high=100.04,
                         seg_taker_sell=5.0, seg_taker_buy=5.0) is None


class TestRiskLimits:
    def _book(self, symbol="BTC", qty=1.0, px=100.0):
        b = InventoryBook()
        b.apply_fill(symbol=symbol, side="buy", qty=qty, fill_px=px, mid_px=px, now_ts=1000.0)
        return b

    def test_normal_allow(self):
        ok, why = check_lane_limits(symbol="BTC", book=InventoryBook(), marks={},
                                    equity=10000.0, now_ts=1000.0)
        assert ok and why == ""

    def test_vol_pause(self):
        ok, why = check_lane_limits(symbol="BTC", book=InventoryBook(), marks={},
                                    equity=10000.0, sigma_norm=2.0)
        assert not ok and "vol_pause" in why

    def test_toxic_streak(self):
        ok, why = check_lane_limits(symbol="BTC", book=InventoryBook(), marks={},
                                    equity=10000.0, toxic_streak=3)
        assert not ok and "toxic" in why

    def test_symbol_exposure_limit(self):
        b = self._book(qty=20.0)     # 名义 2000 > 10000×10%
        ok, why = check_lane_limits(symbol="BTC", book=b, marks={"BTC": 100.0}, equity=10000.0)
        assert not ok and "symbol_exposure" in why

    def test_one_side_too_long(self):
        b = self._book(qty=1.0)      # 名义 100 < 1000，不触发敞口
        ok, why = check_lane_limits(symbol="BTC", book=b, marks={"BTC": 100.0},
                                    equity=10000.0, now_ts=2000.0)  # 持仓 1000s > 300s
        assert not ok and why == "one_side_too_long"

    def test_zero_equity(self):
        ok, why = check_lane_limits(symbol="BTC", book=InventoryBook(), marks={}, equity=0.0)
        assert not ok


class TestEdgeMetric:
    def test_per_trade_expectancy(self):
        m = edge_metric_from_ledger(spread_bp_sum=100.0, fee_bp_sum=-8.0,
                                    price_bp_sum=-20.0, funding_bp_sum=6.0,
                                    slippage_bp_sum=-2.0, n=20)
        assert m["n"] == 20
        assert m["gross_bp"] == pytest.approx(106.0 / 20, abs=1e-4)
        assert m["cost_bp"] == pytest.approx(10.0 / 20, abs=1e-4)
        assert m["net_bp"] == pytest.approx(76.0 / 20, abs=1e-4)

    def test_zero_n_safe(self):
        m = edge_metric_from_ledger(spread_bp_sum=0, fee_bp_sum=0, price_bp_sum=0,
                                    funding_bp_sum=0, slippage_bp_sum=0, n=0)
        assert m["n"] == 0 and m["net_bp"] == 0.0
