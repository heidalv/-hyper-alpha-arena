# -*- coding: utf-8 -*-
"""[F60] L1 做市车道驱动器（纯逻辑层）单测。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.runner import (  # noqa: E402
    SymbolState,
    check_data_freshness,
    check_fee_guard,
    plan_tick,
    update_sigma,
)


class TestSigmaNorm:
    """波动归一必须只看滚动价差窗口，不能依赖跨时段价格水平。

    事故背景：旧实现用「上次挂单时的中价」做基准，Aster 断流 22 天后
    中价从 64k 变 79k → sigma=452 → 永久 vol_pause。
    """

    def test_zero_until_window_fills(self):
        st = SymbolState(symbol="BTC")
        for _ in range(19):
            assert update_sigma(st, 0.0005) == 0.0
        assert st.spread_baseline == 0.0

    def test_baseline_set_when_window_full(self):
        st = SymbolState(symbol="BTC")
        for _ in range(20):
            update_sigma(st, 0.0005)
        assert st.spread_baseline == pytest.approx(0.0005)
        assert update_sigma(st, 0.0005) == pytest.approx(0.0, abs=1e-12)

    def test_spread_widening_raises_sigma(self):
        st = SymbolState(symbol="BTC")
        for _ in range(20):
            update_sigma(st, 0.0005)
        # 价差翻倍 → 窗口均值上升 → sigma > 0
        for _ in range(10):
            s = update_sigma(st, 0.0010)
        assert s > 0.2

    def test_price_level_change_does_not_affect_sigma(self):
        st = SymbolState(symbol="BTC")
        for _ in range(20):
            update_sigma(st, 0.0005)
        st.quote_mid = 64000.0          # 22 天前的中价
        # 价格翻倍不进入 sigma 计算（输入只有相对价差）
        assert update_sigma(st, 0.0005) == pytest.approx(0.0, abs=1e-12)

    def test_invalid_input_ignored(self):
        st = SymbolState(symbol="BTC")
        assert update_sigma(st, 0.0) == 0.0
        assert update_sigma(st, -1.0) == 0.0
        assert st.spread_hist == []

    def test_state_roundtrip_keeps_window(self):
        st = SymbolState(symbol="BTC")
        for _ in range(25):
            update_sigma(st, 0.0007)
        back = SymbolState.from_dict(st.to_dict())
        assert len(back.spread_hist) == 20
        assert back.spread_baseline == pytest.approx(st.spread_baseline)


class TestDataFreshness:
    """数据新鲜度闸门 —— 防止拿陈旧盘口挂单。

    事故背景：asterdex 的盘口/成交采集在 2026-08-18 停止，最新快照陈旧 22 天
    （BTC 快照 64k、实际 78k）。没有这道闸门，影子期会按 22 天前的价格报价。
    """

    def test_fresh_snapshot_passes(self):
        now = 1_800_000_000.0
        ok, age, why = check_data_freshness(int(now * 1000) - 30_000, now)
        assert ok is True and age == pytest.approx(30.0) and why == ""

    def test_stale_snapshot_blocked(self):
        now = 1_800_000_000.0
        ok, age, why = check_data_freshness(int(now * 1000) - 22 * 86400 * 1000, now)
        assert ok is False and age > 1_000_000
        assert why.startswith("stale_data(")

    def test_missing_snapshot_blocked(self):
        ok, age, why = check_data_freshness(0, 1_800_000_000.0)
        assert ok is False and why == "no_snapshot"

    def test_threshold_is_configurable(self):
        now = 1_800_000_000.0
        ts = int(now * 1000) - 120_000
        assert check_data_freshness(ts, now, max_age_sec=180)[0] is True
        assert check_data_freshness(ts, now, max_age_sec=60)[0] is False

    def test_zero_threshold_disables_check(self):
        now = 1_800_000_000.0
        ts = int(now * 1000) - 22 * 86400 * 1000
        assert check_data_freshness(ts, now, max_age_sec=0)[0] is True


class TestFeeGuard:
    def test_zero_maker_fee_allowed(self):
        ok, why = check_fee_guard(0.0)
        assert ok is True and why == ""

    def test_rebate_allowed(self):
        ok, why = check_fee_guard(-1.0)
        assert ok is True and why == "rebate"

    def test_at_threshold_allowed(self):
        ok, _ = check_fee_guard(0.5, max_bp=0.5)
        assert ok is True

    def test_above_threshold_blocks(self):
        ok, why = check_fee_guard(0.6, max_bp=0.5)
        assert ok is False and "maker_fee_too_high" in why

    def test_two_bp_blocks(self):
        # F52 实测：maker 涨到 2bp，5bp 挂宽的边际基本归零 → 必须停车道
        ok, _ = check_fee_guard(2.0)
        assert ok is False


class TestSymbolState:
    def test_roundtrip(self):
        st = SymbolState(symbol="BTC", qty=1.5, avg_px=100.0, avg_mid=100.1,
                         opened_ts=10.0, last_ts=20.0, quote_bid=99.9, quote_ask=100.1,
                         quote_mid=100.0, quote_ts=20.0, toxic_streak=2)
        back = SymbolState.from_dict(st.to_dict())
        assert back == st

    def test_from_dict_tolerates_missing(self):
        st = SymbolState.from_dict({"symbol": "ETH"})
        assert st.symbol == "ETH" and st.qty == 0.0 and st.toxic_streak == 0


class TestPlanTick:
    def test_no_mid_skips(self):
        dec, _ = plan_tick(state=SymbolState(symbol="BTC"), mid=0.0,
                           seg_low=0, seg_high=0, seg_taker_sell=0, seg_taker_buy=0,
                           now_ts=1.0)
        assert dec.action == "pause" and dec.skip == "no_mid"

    def test_first_tick_quotes_both_sides(self):
        st = SymbolState(symbol="BTC")
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=100.0, seg_high=100.0,
                           seg_taker_sell=0, seg_taker_buy=0, now_ts=1000.0)
        assert dec.action == "quote"
        assert dec.bid == pytest.approx(99.95)      # 5bp
        assert dec.ask == pytest.approx(100.05)
        assert st.quote_bid == pytest.approx(99.95) and st.quote_ts == 1000.0
        assert dec.fills == []

    def test_resting_bid_fills_on_taker_sell_sweep(self):
        st = SymbolState(symbol="BTC", quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.0, seg_high=100.2,
                           seg_taker_sell=5.0, seg_taker_buy=0.0, now_ts=1014.0,
                           fill_notional=100.0)
        assert len(dec.fills) == 1
        f = dec.fills[0]
        assert f.side == "buy" and f.px == pytest.approx(99.95)
        assert f.edge_bp == pytest.approx(5.0, abs=1e-6)
        # 固定基础币数量 = 名义 / 中价
        assert st.qty == pytest.approx(1.0, rel=1e-12)

    def test_spread_attribution_uses_quote_mid_not_fill_mid(self):
        """归因回归：行情下跌时，负值必须落在 price 维度而不是 spread。

        旧实现用「成交判定时的中价」做基准，把 8bp 的挂宽算成负价差，
        看起来像「挂宽 8bp 却负捕获」，实际是逆选择。
        """
        st = SymbolState(symbol="BTC", quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        # 中价从 100 跌到 99.5，买单价 99.95 被卖方打穿
        dec, _ = plan_tick(state=st, mid=99.5, seg_low=99.0, seg_high=99.8,
                           seg_taker_sell=5.0, seg_taker_buy=0.0, now_ts=1014.0,
                           fill_notional=100.0,
                           limits=LaneRiskLimits(stop_loss_bp=0.0))
        f = dec.fills[0]
        # 相对**挂单时**的中价 100：买 99.95 → +5bp（挂宽捕获为正）
        assert f.edge_bp == pytest.approx(5.0, abs=1e-6)
        assert f.spread_usd > 0
        # 相对成交时的中价 99.5 则是负的（逆选择），但那是 price 维度的事
        assert (99.5 - 99.95) / 99.5 * 1e4 < 0
        assert st.avg_mid == pytest.approx(100.0)

    def test_resting_ask_fills_on_taker_buy_sweep(self):
        st = SymbolState(symbol="BTC", quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.9, seg_high=101.0,
                           seg_taker_sell=0.0, seg_taker_buy=5.0, now_ts=1014.0)
        assert len(dec.fills) == 1
        assert dec.fills[0].side == "sell"
        assert st.qty < 0

    def test_both_sides_fill_in_one_interval(self):
        st = SymbolState(symbol="BTC", quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.0, seg_high=101.0,
                           seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1014.0)
        assert [f.side for f in dec.fills] == ["buy", "sell"]
        # 固定基础币数量 → 一买一卖后库存精确归零
        assert st.qty == pytest.approx(0.0, abs=1e-12)

    def test_no_fill_without_penetration(self):
        st = SymbolState(symbol="BTC", quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.96, seg_high=100.04,
                           seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1014.0)
        assert dec.fills == []

    def test_timeout_flattens_at_touch_with_taker_fee(self):
        st = SymbolState(symbol="BTC", qty=1.0, avg_px=99.95, avg_mid=100.0,
                         opened_ts=1000.0, quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
                           seg_taker_sell=0, seg_taker_buy=0, now_ts=1400.0,
                           half_spread=0.05,
                           limits=LaneRiskLimits(max_one_side_seconds=300.0))
        assert dec.action == "flatten"
        fl = [f for f in dec.fills if f.is_flatten]
        assert len(fl) == 1 and fl[0].side == "sell"
        # 打 bid：mid − 半价差
        assert fl[0].px == pytest.approx(99.95)
        assert st.qty == pytest.approx(0.0, abs=1e-9)
        assert st.opened_ts == 0.0

    def test_no_flatten_before_timeout(self):
        st = SymbolState(symbol="BTC", qty=1.0, avg_px=99.95, avg_mid=100.0,
                         opened_ts=1000.0, quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.9, seg_high=100.1,
                           seg_taker_sell=0, seg_taker_buy=0, now_ts=1100.0)
        assert all(not f.is_flatten for f in dec.fills)
        assert st.qty == pytest.approx(1.0)

    def test_vol_pause_blocks_both_sides(self):
        st = SymbolState(symbol="BTC", quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=100.0, seg_high=100.0,
                           seg_taker_sell=0, seg_taker_buy=0, now_ts=1014.0,
                           sigma_norm=2.0)
        assert dec.action == "pause" and dec.skip == "vol_pause"
        assert st.quote_bid == 0.0 and st.quote_ask == 0.0

    def test_exposure_block_is_one_sided(self):
        # 多头库存已到单币上限（$500 = 权益 5000 × 10%）→ 只允许减仓的卖腿
        st = SymbolState(symbol="BTC", qty=5.0, avg_px=100.0, avg_mid=100.0,
                         opened_ts=1000.0, quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=100.0, seg_high=100.0,
                           seg_taker_sell=0, seg_taker_buy=0, now_ts=1014.0,
                           fill_notional=100.0)
        assert dec.action == "quote"
        assert dec.bid == 0.0 and dec.ask > 0.0
        assert dec.skip == "symbol_exposure" and dec.skip_side == "buy"

    def test_short_inventory_blocks_ask_only(self):
        st = SymbolState(symbol="BTC", qty=-5.0, avg_px=100.0, avg_mid=100.0,
                         opened_ts=1000.0, quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=100.0, seg_high=100.0,
                           seg_taker_sell=0, seg_taker_buy=0, now_ts=1014.0,
                           fill_notional=100.0)
        assert dec.bid > 0.0 and dec.ask == 0.0
        assert dec.skip_side == "sell"

    def test_reducing_side_quotes_tighter_than_adding_side(self):
        # 多头库存 60% 上限 → 卖腿（减仓）应比买腿（加仓）更贴盘口
        st = SymbolState(symbol="BTC", qty=3.0, avg_px=100.0, avg_mid=100.0,
                         opened_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=100.0, seg_high=100.0,
                           seg_taker_sell=0, seg_taker_buy=0, now_ts=1014.0,
                           params=QuoteParams(k_inv=0.9))
        assert dec.w_ask_bp < dec.w_bid_bp

    def test_toxic_streak_increments_on_adverse_move(self):
        """毒性流 = 成交后中价继续朝不利方向走（逆选择），不是价差本身为负。"""
        st = SymbolState(symbol="BTC", quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        # 挂单时中价 100，成交判定时中价 99.0 → 买完后中价继续跌 100bp
        plan_tick(state=st, mid=99.0, seg_low=98.0, seg_high=99.5,
                  seg_taker_sell=5.0, seg_taker_buy=0.0, now_ts=1014.0,
                  limits=LaneRiskLimits(toxic_bp=15.0))
        assert st.toxic_streak >= 1

    def test_toxic_streak_resets_when_move_is_small(self):
        st = SymbolState(symbol="BTC", quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        plan_tick(state=st, mid=99.99, seg_low=99.0, seg_high=100.2,
                  seg_taker_sell=5.0, seg_taker_buy=0.0, now_ts=1014.0,
                  limits=LaneRiskLimits(toxic_bp=15.0))
        assert st.toxic_streak == 0


class TestOneSidedFillDetection:
    """[F72] 回归：一侧挂单为 0 时，另一侧仍必须检查成交。

    事故：`plan_tick` 曾用 `if quote_bid > 0 and quote_ask > 0` 作为成交判定的总开关。
    一旦库存到顶（加仓侧被敞口闸门挡住 → 该侧挂单价置 0），**两侧都不再检查成交**，
    减仓腿永远等不到对手盘 → 只能超时砸单。实测影子期平仓占比 32%（回放 14%）。
    """

    def test_long_at_limit_still_fills_reducing_ask(self):
        """多头到顶仍必须能成交减仓侧（本测试锁定 F72 语义）。

        [F91 2026-09-14 更新] 减仓腿改为**精确平仓**（`min(|现仓|, 队列份额)`）：
        旧口径按 dollar 腿量 $100/100=1.0 平，新口径按现仓 2.0 但受队列份额
        （5.0 × 0.30 = 1.5）封顶 ⇒ 成交 1.5、剩 0.5。语义未变：减仓侧照常成交。
        """
        st = SymbolState(symbol="BTC", qty=2.0, avg_px=100.0, avg_mid=100.0,
                         opened_ts=1000.0, quote_bid=0.0, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.9, seg_high=100.5,
                           seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1014.0,
                           fill_notional=100.0,
                           limits=LaneRiskLimits(max_one_side_seconds=900,
                                                 max_net_directional_ratio=0.04,
                                                 stop_loss_bp=0.0))
        assert [f.side for f in dec.fills] == ["sell"]
        assert dec.fills[0].qty == pytest.approx(1.5, abs=1e-6), "min(现仓 2.0, 5.0×0.30)"
        assert st.qty == pytest.approx(0.5, abs=1e-6)

    def test_short_at_limit_still_fills_reducing_bid(self):
        """空头到顶仍必须能成交减仓侧（对称保护，口径同 F91）。"""
        st = SymbolState(symbol="ETH", qty=-2.0, avg_px=100.0, avg_mid=100.0,
                         opened_ts=1000.0, quote_bid=99.95, quote_ask=0.0,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.5, seg_high=100.1,
                           seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1014.0,
                           fill_notional=100.0,
                           limits=LaneRiskLimits(max_one_side_seconds=900,
                                                 max_net_directional_ratio=0.04,
                                                 stop_loss_bp=0.0))
        assert [f.side for f in dec.fills] == ["buy"]
        assert dec.fills[0].qty == pytest.approx(1.5, abs=1e-6)
        assert st.qty == pytest.approx(-0.5, abs=1e-6)

    def test_no_quotes_no_fill(self):
        st = SymbolState(symbol="BTC", quote_bid=0.0, quote_ask=0.0)
        dec, _ = plan_tick(state=st, mid=100.0, seg_low=99.0, seg_high=101.0,
                           seg_taker_sell=5.0, seg_taker_buy=5.0, now_ts=1014.0)
        assert dec.fills == []


class TestTailGatesInTick:
    """[F71] 止损与趋势闸门在 plan_tick 中的行为。"""

    def test_stop_loss_flattens_early(self):
        # 多头持仓，中价已跌 40bp（浮亏超 25bp 阈值）→ 不等超时就平
        st = SymbolState(symbol="BTC", qty=1.0, avg_px=100.0, avg_mid=100.0,
                         opened_ts=1000.0, quote_bid=99.95, quote_ask=100.05,
                         quote_mid=100.0, quote_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=99.6, seg_low=99.5, seg_high=99.9,
                           seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1014.0,
                           half_spread=0.05,
                           limits=LaneRiskLimits(stop_loss_bp=25.0,
                                                 max_one_side_seconds=3600.0))
        fl = [f for f in dec.fills if f.is_flatten]
        assert len(fl) == 1 and fl[0].side == "sell"
        assert st.qty == pytest.approx(0.0, abs=1e-9)
        assert dec.skip == "stop_loss"

    def test_stop_loss_not_triggered_within_threshold(self):
        st = SymbolState(symbol="BTC", qty=1.0, avg_px=100.0, avg_mid=100.0,
                         opened_ts=1000.0)
        dec, _ = plan_tick(state=st, mid=99.9, seg_low=99.8, seg_high=100.0,
                           seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1014.0,
                           limits=LaneRiskLimits(stop_loss_bp=25.0,
                                                 max_one_side_seconds=3600.0))
        assert all(not f.is_flatten for f in dec.fills)
        assert st.qty == pytest.approx(1.0)

    def test_stop_loss_disabled(self):
        st = SymbolState(symbol="BTC", qty=1.0, avg_px=100.0, avg_mid=100.0,
                         opened_ts=1000.0)
        plan_tick(state=st, mid=90.0, seg_low=89.0, seg_high=90.5,
                  seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1014.0,
                  limits=LaneRiskLimits(stop_loss_bp=0.0, max_one_side_seconds=3600.0))
        assert st.qty == pytest.approx(1.0)

    def test_trend_down_blocks_bid_only(self):
        # 近 20 期中价单边下跌 50bp → 禁止买（逆势），卖腿仍可挂
        st = SymbolState(symbol="BTC", mid_hist=[100.0] * 19 + [99.5])
        dec, _ = plan_tick(state=st, mid=99.5, seg_low=99.5, seg_high=99.5,
                           seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1014.0,
                           limits=LaneRiskLimits(trend_pause_bp=30.0))
        assert dec.bid == 0.0 and dec.ask > 0.0
        assert dec.skip == "trend_down" and dec.skip_side == "buy"

    def test_trend_up_blocks_ask_only(self):
        st = SymbolState(symbol="BTC", mid_hist=[100.0] * 19 + [100.5])
        dec, _ = plan_tick(state=st, mid=100.5, seg_low=100.5, seg_high=100.5,
                           seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1014.0,
                           limits=LaneRiskLimits(trend_pause_bp=30.0))
        assert dec.bid > 0.0 and dec.ask == 0.0
        assert dec.skip == "trend_up" and dec.skip_side == "sell"

    def test_trend_gate_off_by_default(self):
        st = SymbolState(symbol="BTC", mid_hist=[100.0] * 19 + [99.0])
        dec, _ = plan_tick(state=st, mid=99.0, seg_low=99.0, seg_high=99.0,
                           seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=1014.0)
        assert dec.bid > 0.0 and dec.ask > 0.0


class TestShadowArchive:
    """影子期报告落库（DB 往返；无 DB 时跳过）。"""

    def test_archive_and_read_back(self):
        from uuid import uuid4

        from backend.services.market_maker.runner import ShadowRunner

        lane = f"test_lane_f60_{uuid4().hex[:8]}"
        r = ShadowRunner(lane_id=lane, symbols=["BTC"])
        try:
            if not r.archive_report(days=7):
                pytest.skip("DB 不可用")
            from sqlalchemy import text

            from backend.core.tenant import system_identity
            from backend.database.connection import SessionLocal

            with system_identity():
                with SessionLocal() as db:
                    rows = db.execute(text(
                        "SELECT fills, net_bp, net_usd, window_days FROM lane_shadow_report"
                        " WHERE lane_id=:l ORDER BY id DESC LIMIT 1"
                    ), {"l": lane}).mappings().all()
            assert rows and rows[0]["window_days"] == 7
            # 无成交时 net_usd 必须是 NULL（不是 0），否则「没跑」会被读成「跑平了」
            assert rows[0]["net_usd"] is None
        finally:
            try:
                from sqlalchemy import text

                from backend.core.tenant import system_identity
                from backend.database.connection import SessionLocal

                with system_identity():
                    with SessionLocal() as db:
                        db.execute(text("DELETE FROM lane_shadow_report WHERE lane_id=:l"),
                                   {"l": lane})
                        db.commit()
            except Exception:
                pass
