# -*- coding: utf-8 -*-
"""[2026-09-14 F91] 减仓腿「精确平仓」契约（库存漂移回归）。

缺陷现场（线上账本 BTC 行）：
    12:43:49 sell 0.00386433 (mid 77632.35)  → 净 -0.00379905
    12:44:19 buy  0.00386497 (mid 77625.29)  → 净 +0.00006592   ← 每趟留 +6.4e-7
两腿都用 `leg_qty = fill_notional/mid`，而两腿 mid 不同 ⇒ 每次往返留下 |Δqty|
残差；因均值回归（进在有利极端、出在回归后）两个方向**同号** ⇒ 系统性单向库存
漂移。后果：`lane_ledger` 能重建出运行态根本不存在的持仓（BTC $5.10 幽灵仓）、
净敞口上限被未登记库存悄悄占用、已实现盈亏与真实库存对不上。

修：减仓方向取 `min(|现仓|, 队列份额)` —— 平仓精确归零（进场腿仍用 dollar 腿量）。

⚠️ 时间口径：本文件统一用 `T0`（秒）作为"当前时刻"，运行态时间戳与 `now_ts`
必须同纪元，否则 F89a 陈旧挂单保护/超时平仓会（正确地）介入，测的就不是本契约。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    LaneRiskLimits,
    QuoteParams,
)

T0 = 1_700_000_000.0          # 统一纪元（秒）


def _limits():
    return LaneRiskLimits(trend_pause_bp=0.0, vol_pause_mult=0.0, vol_pause_sigma=0.0,
                          max_symbol_notional_ratio=1.0, max_net_directional_ratio=1.0,
                          max_net_exposure_ratio=1.0, max_quote_age_sec=90.0,
                          ofi_block_threshold=0.0, ofi_flatten_threshold=0.0,
                          stop_loss_bp=0.0, max_one_side_seconds=10_000.0)


def _state(qty=0.0, avg_px=0.0, avg_mid=0.0, opened_ts=0.0, bid=99.0, ask=101.0):
    st = mmrunner.SymbolState(symbol="BTC")
    st.mid_hist = [100.0] * 60
    st.qty, st.avg_px, st.avg_mid, st.opened_ts = qty, avg_px, avg_mid, opened_ts
    st.quote_bid, st.quote_ask, st.quote_ts, st.quote_mid = bid, ask, T0, 100.0
    return st


def _tick(st, mid, *, only, seg_sell=1e9, seg_buy=1e9, now=T0 + 15.0,
          fill_notional=300.0):
    """只让 `only` 一侧的挂单被区间穿越（用当前挂单价推区间上下界）。"""
    if only == "buy":
        seg_low, seg_high = st.quote_bid - 0.5, min(st.quote_ask, mid)
    else:
        seg_low, seg_high = max(st.quote_bid, mid), st.quote_ask + 0.5
    dec, _ = mmrunner.plan_tick(
        state=st, mid=mid, seg_low=seg_low, seg_high=seg_high,
        seg_taker_sell=seg_sell, seg_taker_buy=seg_buy, now_ts=now,
        equity=300.0, fill_notional=fill_notional, params=QuoteParams(),
        limits=_limits(),
    )
    assert dec.skip != "stale_quote_cleared", "夹具时间口径错误（挂单被判陈旧）"
    return dec


# ── ① 精确平仓 ──────────────────────────────────────────────────────────────

def test_closing_leg_closes_exactly_at_different_mid():
    """已持多头、行情走动后卖出成交 ⇒ 必须精确归零。

    旧行为：卖出腿取 fill_notional/mid ⇒ $300 名义远大于 $100 持仓，会把仓位
    **翻成空头**（或留残差），这是漂移之源。
    """
    st = _state(qty=1.0, avg_px=100.0, avg_mid=100.0, opened_ts=T0)
    dec = _tick(st, mid=101.0, only="sell")
    sells = [f for f in dec.fills if f.side == "sell" and not f.is_flatten]
    assert sells, f"挂单卖出腿应成交: {dec.fills}"
    assert sum(f.qty for f in sells) == pytest.approx(1.0, rel=1e-9), \
        "平仓腿必须精确等于现仓数量"
    assert st.qty == pytest.approx(0.0, abs=1e-12), "平仓后库存必须归零，不得翻转"
    assert st.avg_px == 0.0 and st.avg_mid == 0.0 and st.opened_ts == 0.0


def test_entry_leg_still_uses_dollar_notional():
    """空仓进场腿仍按 dollar 腿量（$300 / mid），不受 F91 影响。"""
    dec = _tick(_state(), mid=100.0, only="buy")
    buys = [f for f in dec.fills if f.side == "buy"]
    assert buys, f"买单应成交: {dec.fills}"
    assert sum(f.qty for f in buys) == pytest.approx(3.0, rel=1e-6), \
        "$300 / $100 = 3.0 基础币"


def test_short_position_reduces_exactly_by_buying():
    """空头持仓 ⇒ 买入腿精确回补（对称性保护）。"""
    st = _state(qty=-2.0, avg_px=100.0, avg_mid=100.0, opened_ts=T0)
    dec = _tick(st, mid=100.0, only="buy")
    buys = [f for f in dec.fills if f.side == "buy" and not f.is_flatten]
    assert buys and sum(f.qty for f in buys) == pytest.approx(2.0, rel=1e-9)
    assert st.qty == pytest.approx(0.0, abs=1e-12)


def test_reducing_leg_cannot_flip_position():
    """减仓腿的成交量不得把仓位翻向（旧行为会用 $300 腿量翻仓）。"""
    st = _state(qty=0.5, avg_px=100.0, avg_mid=100.0, opened_ts=T0)
    dec = _tick(st, mid=101.0, only="sell")
    assert st.qty >= -1e-12, f"平多不得变成空头: qty={st.qty}"
    for f in dec.fills:
        assert f.qty <= 0.5 + 1e-9, f"减仓腿不得超过现仓: {f}"


# ── ② 漂移回归 ──────────────────────────────────────────────────────────────

def test_round_trip_leaves_no_residual_across_moving_mids():
    """连续往返（每趟 mid 不同）后库存必须精确归零——漂移的直接回归测试。

    复刻线上形态：100 买 → 101 卖 → 100 买 → 101 卖 …，每趟都换 mid。
    """
    st = _state()
    t = T0
    for i in range(6):
        t += 15.0
        _tick(st, mid=100.0, only="buy", now=t)
        assert st.qty > 0, f"第 {i} 趟应建立多头"
        # 新挂单在 100 附近；行情走到 101 → 只有卖腿被穿越
        st.quote_ts = t
        t += 15.0
        _tick(st, mid=101.0, only="sell", now=t)
        st.quote_ts = t
        assert st.qty == pytest.approx(0.0, abs=1e-12), \
            f"第 {i} 趟往返后不得残留库存（漂移）: qty={st.qty}"
    assert st.avg_px == 0.0 and st.avg_mid == 0.0


def test_queue_share_still_caps_the_closing_leg():
    """精确平仓不得绕过队列份额：主动量不足时只能部分平仓。"""
    st = _state(qty=3.0, avg_px=100.0, avg_mid=100.0, opened_ts=T0)
    dec = _tick(st, mid=101.0, only="sell", seg_buy=1.0)   # 1.0 × 0.30 = 0.3 币
    sells = [f for f in dec.fills if f.side == "sell" and not f.is_flatten]
    assert sells, f"应有挂单卖出成交: {dec.fills}"
    assert sum(f.qty for f in sells) == pytest.approx(0.3, rel=1e-6), \
        "队列份额必须仍然封顶（min(现仓, 主动量×份额)）"
    assert 0 < st.qty < 3.0, "未平完的仓位应留在运行态（由超时/止损路径收敛）"


def test_flatten_paths_still_close_full_position():
    """超时平仓路径必须最终把仓位归零（回归保护，勿被 F91 误改）。

    同一 tick 内的正确顺序：先判挂单被动成交（减仓腿精确平掉一部分），再让
    超时路径打对手价平掉**剩余全部**。两者合计 = 原仓位。
    """
    st = _state(qty=3.0, avg_px=100.0, avg_mid=100.0, opened_ts=T0 - 20_000.0)
    dec = _tick(st, mid=101.0, only="sell", seg_buy=1.0)
    flats = [f for f in dec.fills if f.is_flatten]
    assert flats, f"超时应打对手价平仓: {dec.fills}"
    total = sum(f.qty for f in dec.fills)
    assert total == pytest.approx(3.0, rel=1e-9), \
        f"被动减仓 + 超时平仓 必须合计平掉全仓: {total}"
    assert st.qty == pytest.approx(0.0, abs=1e-12)
    assert flats[0].px == pytest.approx(101.0), "超时平仓打对手价（半价差=0 时=中价）"
