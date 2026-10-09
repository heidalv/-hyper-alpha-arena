# -*- coding: utf-8 -*-
"""[h402 2026-09-27] #13 挂单时刻入账（quote_ts）回归测试。

依据（h369）：成交时刻的 OFI 归因被机械污染（挂单→成交的 15~30s 桶延迟），
挂单时刻才能做正确的流条件归因。实现：PlannedFill.quote_ts = state.quote_ts
（加仓腿；judge_lag_buckets=0 时即真实挂出时刻），强平腿 = 0；写入
lane_ledger.meta_json.quote_ts。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402


def _limits():
    return LaneRiskLimits(stop_loss_bp=40.0, take_profit_bp=0.0,
                          stop_maker_grace_sec=0.0, min_hold_seconds=0.0,
                          trend_pause_bp=0.0, ofi_confirm_threshold=0.0,
                          ofi_block_threshold=0.0, max_one_side_seconds=3600.0)


def test_planned_fill_default_and_serialization():
    f = mmrunner.PlannedFill(symbol="BTC", side="buy", qty=1.0, px=100.0,
                             mid=100.0, ts=1_000_100.0)
    assert f.quote_ts == 0.0
    d = f.to_dict()
    assert "quote_ts" in d and d["quote_ts"] == 0.0


def test_add_fill_carries_quote_ts():
    """加仓腿：quote_ts = 被判定挂单的挂出时刻（state.quote_ts）。"""
    now = 1_000_100.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.0
    st.quote_bid = 99.9
    st.quote_mid = 100.0
    st.quote_ts = now - 12.5        # 挂出时刻 = 12.5s 前
    dec, _ = mmrunner.plan_tick(
        state=st, mid=100.0, seg_low=99.8, seg_high=100.2,
        seg_taker_sell=100.0, seg_taker_buy=0.0, now_ts=now,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=_limits())
    adds = [f for f in dec.fills if not f.is_flatten]
    assert adds, f"应有加仓腿成交，skip={dec.skip}"
    assert abs(adds[0].quote_ts - (now - 12.5)) < 1e-6, \
        f"quote_ts 应=挂单时刻，实际 {adds[0].quote_ts}"


def test_flatten_fill_quote_ts_zero():
    """强平腿（止损）：quote_ts = 0（非挂单驱动，不参与流归因）。"""
    now = 1_000_100.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = now - 120.0
    dec, _ = mmrunner.plan_tick(
        state=st, mid=99.5, seg_low=99.4, seg_high=99.6,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now,
        equity=5000.0, fill_notional=100.0,
        params=QuoteParams(), limits=_limits())
    flats = [f for f in dec.fills if f.is_flatten]
    assert flats, f"−50bp 应触发止损，skip={dec.skip}"
    assert flats[0].quote_ts == 0.0


def test_ledger_write_source_contract():
    """源码契约：_record_fills 必须把 quote_ts 写进 lane_ledger meta。"""
    import inspect

    from backend.services.market_maker.runner import ShadowRunner
    src = inspect.getsource(ShadowRunner._record_fills)
    assert '"quote_ts"' in src, "_record_fills 必须写入 meta_json.quote_ts"
