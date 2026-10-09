# -*- coding: utf-8 -*-
"""[F278 2026-09-16] 步长/最小名义合规闸契约：

纸面车道必须与实盘共用**同一物理约束**——否则会记录"实盘必被拒"的成交
（BTCUSDT stepSize=0.001 BTC ≈ $75.83 > $30 单腿 ⇒ 腿量只能取 0 或 0.001）。

契约：
  · 腿量按 step 向下取整后 < min_qty 或 < min_notional ⇒ 不合规（停报该币）；
  · 表外币种 fail-open（缺表不得把车道整死）；
  · 权益放大到 腿 ≥ 一步名义 时**自动恢复**报价（不是永久禁用）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook, LaneRiskLimits, QuoteParams, leg_qty_compliant,
)
from backend.services.market_maker.runner import SymbolState, plan_tick  # noqa: E402

BTC_PX = 75830.0    # 一步 = 0.001 BTC ≈ $75.83
ETH_PX = 2400.0     # 一步 = 0.001 ETH ≈ $2.40


def _limits():
    return LaneRiskLimits(ofi_block_threshold=0.0, vol_pause_sigma=0.0,
                          trend_pause_bp=0.0, stop_loss_bp=0.0,
                          max_one_side_seconds=3600.0,
                          max_net_directional_ratio=0.3, max_net_exposure_ratio=0.6,
                          max_gross_notional_ratio=1.0)


def _tick(symbol: str, px: float, leg: float, equity: float = 300.0):
    st = SymbolState(symbol=symbol, qty=0.0, avg_px=0.0, avg_mid=0.0, opened_ts=0.0)
    dec, _ = plan_tick(
        state=st, mid=px, seg_low=0.0, seg_high=0.0,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=time.time(),
        params=QuoteParams(w_base_bp=10.0, k_inv=0.5, frozen_width_bp=None),
        limits=_limits(), equity=equity, fill_notional=leg,
        taker_fee_bp=4.0, maker_fee_bp=0.0, half_spread=px * 1e-5,
        sigma_norm=0.0, book=InventoryBook(), marks={symbol: px},
        ofi=0.0, day_pnl_usd=0.0,
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        lane_limits_enforce=True)
    return dec


# ───────────────────────── 纯函数 ─────────────────────────

def test_btc_30usd_leg_is_not_tradable():
    """$30 腿 / BTC $75,830 ⇒ 0.000396 BTC 非 0.001 整数倍 ⇒ 不合规。"""
    ok, qty, why = leg_qty_compliant("BTC", 30.0, BTC_PX)
    assert not ok, "BTC $30 腿必须判为不可下单"
    assert qty == 0.0, qty
    assert why.startswith("below_step"), why


def test_eth_30usd_leg_is_tradable():
    """$30 腿 / ETH $2,400 ⇒ 0.0125 → 0.012 ETH（≈$28.8）合规。"""
    ok, qty, why = leg_qty_compliant("ETH", 30.0, ETH_PX)
    assert ok and why == ""
    assert abs(qty - 0.012) < 1e-12, qty


def test_unknown_symbol_fails_open():
    """表外币种不约束（缺表 ≠ 停报）。"""
    ok, qty, why = leg_qty_compliant("FOOBAR", 30.0, 10.0)
    assert ok and why == "no_table"
    assert abs(qty - 3.0) < 1e-12, qty


def test_min_notional_floor():
    """取整后名义 < min_notional($5) ⇒ 不合规。"""
    ok, _qty, why = leg_qty_compliant("DOGE", 3.0, 0.2)   # 15 DOGE = $3 < $5
    assert not ok and why.startswith("below_min_notional"), why


def test_btc_recovers_once_leg_covers_one_step():
    """腿 ≥ 一步名义（$75.83）⇒ 合规恢复（按权益自动放大，非永久禁用）。"""
    ok, qty, why = leg_qty_compliant("BTC", 76.0, BTC_PX)
    assert ok and why == ""
    assert abs(qty - 0.001) < 1e-12, qty


# ───────────────────────── plan_tick 接线 ─────────────────────────

def test_plan_tick_stops_btc_below_step():
    """$30 腿 ⇒ BTC 停报，skip=below_step，且细节可观测。"""
    dec = _tick("BTC", BTC_PX, 30.0)
    assert dec.action == "pause", dec.action
    assert dec.skip == "below_step", dec.skip
    assert dec.bid == 0.0 and dec.ask == 0.0
    assert "below_step" in dec.skip_detail and "0.001" in dec.skip_detail, dec.skip_detail
    assert dec.to_dict()["skip_detail"] == dec.skip_detail, "状态输出必须带细节"


def test_plan_tick_quotes_eth_at_same_leg():
    """同一 $30 腿下 ETH 照常双边报价（闸门只淘汰物理上不可下单的币）。"""
    dec = _tick("ETH", ETH_PX, 30.0)
    assert dec.action == "quote", (dec.action, dec.skip)
    assert dec.bid > 0.0 and dec.ask > 0.0


def test_plan_tick_btc_quotes_when_leg_big_enough():
    """腿放大到 $760（权益 $7,600 档）⇒ BTC 恢复报价。"""
    dec = _tick("BTC", BTC_PX, 760.0, equity=7600.0)
    assert dec.action == "quote", (dec.action, dec.skip)
    assert dec.bid > 0.0 and dec.ask > 0.0
