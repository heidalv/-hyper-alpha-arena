# -*- coding: utf-8 -*-
"""[h472 2026-09-29] `ofi_flatten_maker_only`：同一信号、两种执行。

契约：
  · 默认 0 ⇒ **旧行为逐字一致**：OFI 顺离场方向且年龄 > hold×min_age ⇒
    立即 taker 平仓，落一条 `exit_path=ofi_flatten_taker` 的腿；
  · =1 ⇒ **不穿价**：不产生 taker 腿，封**加仓侧**、减仓侧存活
    （`_tmo_add_block` 机制，F76 豁免），`exit_path=ofi_flatten_maker` 仅作遥测；
  · 年龄不足（< hold×min_age）或 OFI 不顺风 ⇒ 两种模式下都不动作。

依据（h471/h470）：本场馆 maker 费率 0；近 12h 出场全是 taker、手续费占净亏 74%；
ofi_flatten 单路径 102 腿付 −3.53bp/腿 taker 费 + −1.19bp/腿穿价差，而其价格项
+4.89bp/腿、出场后 300s 离场方向有利漂移 +9.91bp（t=2.15）⇒ 该时点被动减仓
本就有对手方流量。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import LaneRiskLimits, QuoteParams  # noqa: E402


def _limits(**kw):
    base = dict(stop_loss_bp=0.0, take_profit_bp=0.0, stop_maker_grace_sec=0.0,
                min_hold_seconds=0.0, trend_pause_bp=0.0, sudden_move_bp=0.0,
                ofi_confirm_threshold=0.0, ofi_block_threshold=0.0,
                max_one_side_seconds=90.0, timeout_exit_maker_only=True,
                ofi_flatten_threshold=0.5, ofi_flatten_min_age_ratio=0.5)
    base.update(kw)
    return LaneRiskLimits(**base)


def _tick(st, mid, now, limits, ofi):
    return mmrunner.plan_tick(
        state=st, mid=mid, seg_low=mid - 0.1, seg_high=mid + 0.1,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=now,
        equity=5000.0, fill_notional=100.0, ofi=ofi,
        params=QuoteParams(w_base_bp=8.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits)


def _long_aged(now, age=60.0):
    """多头持仓、已持 age 秒（> 90×0.5=45s ⇒ 满足 min_age）。"""
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = 0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = now - age
    st.mid_hist = [100.0] * 20
    return st


def test_default_off_is_taker_fill():
    """flag=0（默认）：顺风 OFI ⇒ taker 平仓腿，exit_path=ofi_flatten_taker。"""
    now = 1_000_000.0
    st = _long_aged(now)
    dec, _ = _tick(st, 100.0, now, _limits(ofi_flatten_maker_only=0.0), ofi=0.9)
    flats = [f for f in dec.fills if f.is_flatten]
    assert flats, f"默认应产生 taker 平仓腿，skip={dec.skip}"
    assert dec.exit_path == "ofi_flatten_taker", dec.exit_path
    assert abs(st.qty) < 1e-12, st.qty          # 已平


def test_maker_only_no_taker_fill_and_blocks_add():
    """flag=1：不产生 taker 腿、仓位保留、加仓侧被封（减仓侧存活）。"""
    now = 1_000_000.0
    st = _long_aged(now)
    dec, _ = _tick(st, 100.0, now, _limits(ofi_flatten_maker_only=1.0), ofi=0.9)
    flats = [f for f in dec.fills if f.is_flatten]
    assert not flats, f"maker-only 不应产生 taker 平仓腿：{flats}"
    assert dec.exit_path == "ofi_flatten_maker", dec.exit_path
    assert abs(st.qty - 0.5) < 1e-12, f"仓位应保留，实际 {st.qty}"
    assert "ofi_flatten_maker" in (dec.skip or ""), dec.skip
    # 多头 ⇒ 加仓侧（买）被封（bid=0）、减仓侧（卖）必须存活（ask>0）
    assert dec.bid == 0.0, (dec.bid, dec.ask)
    assert dec.ask > 0.0, (dec.bid, dec.ask)


def test_maker_only_requires_age_and_favorable_ofi():
    """flag=1 但（a）年龄不足 或（b）OFI 不顺风 ⇒ 都不动作（两种模式一致）。"""
    now = 1_000_000.0
    st_young = _long_aged(now, age=10.0)          # 10s < 45s
    dec, _ = _tick(st_young, 100.0, now, _limits(ofi_flatten_maker_only=1.0), ofi=0.9)
    assert dec.exit_path != "ofi_flatten_maker", dec.exit_path
    assert not [f for f in dec.fills if f.is_flatten], dec.fills

    st_against = _long_aged(now)
    dec2, _ = _tick(st_against, 100.0, now, _limits(ofi_flatten_maker_only=1.0),
                    ofi=-0.9)                      # 多头遇卖压 ⇒ 不顺风
    assert dec2.exit_path != "ofi_flatten_maker", dec2.exit_path
    assert not [f for f in dec2.fills if f.is_flatten], dec2.fills


def test_maker_only_short_side_blocks_sell():
    """空头 + 卖压（顺风）⇒ 加仓侧（卖）被封、减仓侧（买）存活。"""
    now = 1_000_000.0
    st = mmrunner.SymbolState(symbol="BTC")
    st.qty = -0.5
    st.avg_px = 100.0
    st.avg_mid = 100.0
    st.opened_ts = now - 60.0
    st.mid_hist = [100.0] * 20
    dec, _ = _tick(st, 100.0, now, _limits(ofi_flatten_maker_only=1.0), ofi=-0.9)
    assert dec.ask == 0.0, (dec.bid, dec.ask)     # 加仓侧（卖）被封
    assert dec.bid > 0.0, (dec.bid, dec.ask)      # 减仓侧（买）存活
    assert abs(st.qty + 0.5) < 1e-12, st.qty
