# -*- coding: utf-8 -*-
"""[h621 2026-09-29] 底层数学模型修复的契约测试。

证据与设计：`_audit_hft_math_model.py`（根因取证）+
`研究结论/底层数学模型根因与修复_20260929.md`。锁四件事：

  1. `aggregate_trades`/`tick_fill_legs`——tick 级聚合与 exact-hit 判腿（纯函数）；
  2. `plan_tick(tick_fill=…)`——发生率（vol>0 消灭幻影）与数量（vol_at_price）
     双口径；`tick_fill=False`（默认）⇒ 与旧行为逐字一致；
  3. `LaneRiskLimits.size_vol_decay`——规模波动衰减只作用**加仓腿**；
  4. `edge_metric_from_ledger`——名义加权口径为**纯新增**（缺省键集不变）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import replay as RP  # noqa: E402
from backend.services.market_maker import runner as R  # noqa: E402
from backend.services.market_maker.core import (  # noqa: E402
    InventoryBook,
    Position,
    QuoteParams,
    LaneRiskLimits,
    aggregate_trades,
    edge_metric_from_ledger,
    quote_visible_at,
    tick_fill_legs,
    tick_trade_hi_ms,
)


# ── 1. 纯函数：aggregate_trades / tick_fill_legs ────────────────────────

@pytest.mark.unit
def test_aggregate_trades_basic():
    """主动卖(bm=True)计入 taker_sell 与 vol_le_bid；主动买反之。"""
    agg = aggregate_trades(
        ts=[1, 2, 3, 4, 5],
        px=[99.90, 100.00, 100.10, 99.95, 100.20],   # low=99.90 high=100.20
        qty=[10.0, 20.0, 30.0, 40.0, 50.0],
        is_buyer_maker=[True, True, False, True, False],
        quote_bid=99.95, quote_ask=100.05,
    )
    assert agg["n"] == 5
    assert agg["seg_low"] == pytest.approx(99.90)
    assert agg["seg_high"] == pytest.approx(100.20)
    assert agg["taker_sell"] == pytest.approx(70.0)   # 10+20+40
    assert agg["taker_buy"] == pytest.approx(80.0)    # 30+50
    # vol_le_bid = 价格 ≤ 99.95 的主动卖：99.90(10) + 99.95(40) = 50
    assert agg["vol_le_bid"] == pytest.approx(50.0)
    # vol_ge_ask = 价格 ≥ 100.05 的主动买：100.10(30) + 100.20(50) = 80
    assert agg["vol_ge_ask"] == pytest.approx(80.0)


@pytest.mark.unit
def test_aggregate_trades_empty_and_no_quote():
    """空数组 ⇒ 全 0；无挂单（quote<=0）⇒ vol 记 0 但聚合照常。"""
    agg = aggregate_trades([], [], [], [])
    assert agg["n"] == 0 and agg["seg_low"] == 0.0 and agg["vol_le_bid"] == 0.0
    agg2 = aggregate_trades([1], [100.0], [5.0], [True])   # 无报价
    assert agg2["taker_sell"] == pytest.approx(5.0)
    assert agg2["vol_le_bid"] == 0.0 and agg2["vol_ge_ask"] == 0.0


@pytest.mark.unit
def test_tick_fill_legs_gating_and_share():
    """可吃量 = 价位真实量 × queue_share；vol=0 / 侧被闸 ⇒ 无腿。"""
    agg = {"vol_le_bid": 100.0, "vol_ge_ask": 40.0}
    legs = tick_fill_legs(agg, quote_bid=99.95, quote_ask=100.05,
                          allow_buy=True, allow_sell=True, queue_share=0.30)
    assert legs["buy"] == pytest.approx(30.0)
    assert legs["sell"] == pytest.approx(12.0)
    # 价格没到（vol=0）⇒ 不成腿 —— 这正是消灭 9.9% 幻影的口径
    legs2 = tick_fill_legs({"vol_le_bid": 0.0, "vol_ge_ask": 40.0},
                           quote_bid=99.95, quote_ask=100.05,
                           allow_buy=True, allow_sell=True, queue_share=0.30)
    assert "buy" not in legs2 and legs2["sell"] == pytest.approx(12.0)
    # 侧闸：allow_buy=False ⇒ 买腿不存在
    legs3 = tick_fill_legs(agg, quote_bid=99.95, quote_ask=100.05,
                           allow_buy=False, allow_sell=True, queue_share=0.30)
    assert "buy" not in legs3


@pytest.mark.unit
def test_tick_trade_hi_ignores_stale_snapshot():
    """15 秒快照落后时，上界仍是墙钟减 3 秒，不能被拉回快照。"""
    wall = 1_790_668_341_669
    snap = wall - 21_000
    assert tick_trade_hi_ms(wall, snap, 3000) == wall - 3000
    # 快照跑到墙钟前面才改用快照
    assert tick_trade_hi_ms(wall, wall + 5000, 3000) == wall + 5000


@pytest.mark.unit
def test_quote_visible_at_skips_quote_newer_than_tape():
    """只取落库截止时刻之前已经挂出的那张，不取刚算出来的新价。"""
    hist = [
        {"ts": 100.0, "bid": 1.0, "ask": 1.1, "mid": 1.05},
        {"ts": 104.0, "bid": 2.0, "ask": 2.1, "mid": 2.05},
    ]
    got = quote_visible_at(hist, 103_000)
    assert got["bid"] == 1.0
    assert quote_visible_at(hist, 99_000) is None


@pytest.mark.unit
def test_plan_tick_tick_fill_counts_exact_touch():
    """成交价恰好等于买价时，逐笔口径算成交。桶路径的严格小于会漏掉这一笔。"""
    b = InventoryBook()
    st = R.SymbolState(symbol="SOLUSDT")
    st.mid_hist = [1.0] * 30
    dec, _meta = R.plan_tick(
        state=st, mid=1.0, seg_low=0.9995, seg_high=0.9995,
        seg_taker_sell=50.0, seg_taker_buy=0.0,
        now_ts=float(time.time()),
        params=QuoteParams(w_base_bp=5.0),
        limits=LaneRiskLimits(),
        book=b, equity=1000.0, fill_notional=100.0,
        half_spread=0.0005, sigma_norm=0.0,
        judged_quote=(0.9995, 1.0005, 1.0),
        tick_fill=True, vol_le_bid=50.0, vol_ge_ask=0.0,
    )
    assert dec.fills, "价位上的精确成交必须记入"


# ── 2. plan_tick 的 tick_fill 分支 ──────────────────────────────────────

def _run_plan_tick(*, tick_fill=False, vol_le_bid=0.0, limits=None,
                   fill_notional=100.0, sigma_norm=0.0):
    """构造一次被判定挂单已穿越的 tick（seg_low < bid）。

    与 test_f342 同一构造法：judged_quote 挂在 mid±5bp，
    seg_low=0.999 < bid=0.9995 ⇒ 旧口径必然判 buy 成交。
    """
    b = InventoryBook()
    st = R.SymbolState(symbol="SOLUSDT")
    st.mid_hist = [1.0] * 30
    dec, _meta = R.plan_tick(
        state=st, mid=1.0, seg_low=0.999, seg_high=1.001,
        seg_taker_sell=2000.0, seg_taker_buy=0.0,
        now_ts=float(time.time()),
        params=QuoteParams(w_base_bp=5.0),
        limits=limits if limits is not None else LaneRiskLimits(),
        book=b, equity=1000.0, fill_notional=fill_notional,
        half_spread=0.0005, sigma_norm=sigma_norm,
        judged_quote=(0.9995, 1.0005, 1.0),
        tick_fill=tick_fill, vol_le_bid=vol_le_bid, vol_ge_ask=0.0,
    )
    return dec


@pytest.mark.unit
def test_plan_tick_tick_fill_blocks_phantom():
    """旧口径判成交、但逐笔价位量为 0 ⇒ tick 口径**不成交**（幻影消灭）。"""
    dec_old = _run_plan_tick()
    assert dec_old.fills, "基准：旧口径（tick_fill=False）价格穿越 ⇒ 必有成交"
    dec_phantom = _run_plan_tick(tick_fill=True, vol_le_bid=0.0)
    assert not dec_phantom.fills, "tick 口径下 vol_le_bid=0 ⇒ 不得成交"


@pytest.mark.unit
def test_plan_tick_tick_fill_caps_qty_by_volume_at_price():
    """tick 口径的腿量 = min(目标, 价位量×QUEUE_SHARE)，不再用整桶总量外推。"""
    dec = _run_plan_tick(tick_fill=True, vol_le_bid=100.0)   # 100 × 0.60 = 60
    assert dec.fills, "价位量>0 ⇒ 成交"
    q = dec.fills[0]
    assert q.qty == pytest.approx(60.0, rel=1e-6), (
        f"腿量应为价位量×QUEUE_SHARE=60，实为 {q.qty}")
    # 对照：旧口径 2000×0.30=600 ≫ 目标 100 ⇒ 腿量=目标（虚记的根源）
    dec_old = _run_plan_tick()
    assert dec_old.fills[0].qty == pytest.approx(100.0, rel=1e-6)


@pytest.mark.unit
def test_plan_tick_bucket_path_verbatim_when_tick_fill_false():
    """tick_fill=False（默认）⇒ 与旧行为逐字一致：腿量用整桶主动量。"""
    dec = _run_plan_tick(tick_fill=False, vol_le_bid=123.0)  # vol 被忽略
    assert dec.fills[0].qty == pytest.approx(100.0, rel=1e-6)


# ── 3. size_vol_decay：规模波动衰减 ────────────────────────────────────

@pytest.mark.unit
def test_size_vol_decay_halves_target_at_sigma_one():
    """k=1、σ=1 ⇒ 加仓腿目标减半；k=0（默认）⇒ 不变。"""
    dec_off = _run_plan_tick(
        limits=LaneRiskLimits(size_vol_decay=0.0), sigma_norm=1.0)
    dec_on = _run_plan_tick(
        limits=LaneRiskLimits(size_vol_decay=1.0), sigma_norm=1.0)
    assert dec_off.fills and dec_on.fills
    assert dec_off.fills[0].qty == pytest.approx(100.0, rel=1e-6)
    assert dec_on.fills[0].qty == pytest.approx(50.0, rel=1e-6), (
        "σ=1、k=1 ⇒ 目标 ÷ (1+1×1) = 50")


@pytest.mark.unit
def test_size_vol_decay_zero_sigma_is_noop():
    """σ=0 ⇒ 衰减因子=1（平静市不加干预）。"""
    dec = _run_plan_tick(
        limits=LaneRiskLimits(size_vol_decay=1.0), sigma_norm=0.0)
    assert dec.fills[0].qty == pytest.approx(100.0, rel=1e-6)


@pytest.mark.unit
def test_size_vol_decay_default_is_off():
    """默认值必须=0（旧行为逐字一致，可一键回退）。"""
    assert LaneRiskLimits().size_vol_decay == 0.0


# ── 4. edge_metric_from_ledger：名义加权口径 ──────────────────────────

@pytest.mark.unit
def test_edge_metric_default_keys_unchanged():
    """缺省不传可选参数 ⇒ 键集与旧版完全一致（纯新增 ✓）。"""
    m = edge_metric_from_ledger(spread_bp_sum=100.0, fee_bp_sum=-8.0,
                                price_bp_sum=-60.0, funding_bp_sum=0.0,
                                slippage_bp_sum=0.0, n=10)
    assert set(m) == {"gross_bp", "cost_bp", "net_bp", "n", "folds"}
    assert m["net_bp"] == pytest.approx(3.2)


@pytest.mark.unit
def test_edge_metric_weighted_exposes_dilution():
    """等权 vs 名义加权：碎腿为正、大腿为负时，等权高估边际。"""
    # 9 笔碎腿（$10）+8bp 与 1 笔大腿（$1000）−2bp：等权 +7.0，加权 −1.27
    bp = [8.0] * 9 + [-2.0]
    notional = [10.0] * 9 + [1000.0]
    m = edge_metric_from_ledger(
        spread_bp_sum=sum(bp), fee_bp_sum=0.0, price_bp_sum=0.0,
        funding_bp_sum=0.0, slippage_bp_sum=0.0, n=10,
        notional_sum=sum(notional),
        net_usd_sum=sum(b / 1e4 * n for b, n in zip(bp, notional)))
    assert m["net_bp"] == pytest.approx(7.0)          # 等权（误导）
    assert m["net_bp_w"] == pytest.approx(
        (9 * 10 * 8 - 1000 * 2) / 1090, rel=1e-3)     # 加权（真实）≈ −1.10


# ── 5. replay 的 tick 分支（注入数据，不碰 DB） ────────────────────────

def _tick_series(price_crosses: bool):
    """120 个 15s 快照（99/101，mid=100）+ 60 笔主动卖。

    quote = mid±5bp ⇒ bid=99.95；crosses=True 的逐笔在 99.90（穿越），
    False 在 99.99（未穿越 ⇒ exact-hit=0）。
    """
    n = 120
    ots = (np.arange(n) * 15000).astype(np.int64)
    bb = np.full(n, 99.0)
    ba = np.full(n, 101.0)
    tts = (np.arange(60) * 30000 + 7000).astype(np.int64)
    px = np.full(60, 99.90 if price_crosses else 99.99)
    qt = np.full(60, 50.0)
    tbm = np.full(60, True)      # 主动卖（打 bid）
    return ots, bb, ba, tts, px, qt, tbm


@pytest.mark.unit
def test_replay_tick_mode_fills_only_on_exact_hit(monkeypatch):
    """tick 模式：价格打到 ⇒ 成交；没打到 ⇒ 0 成交（桶口径会误判的场景）。"""
    monkeypatch.delenv("MM_SEG_SOURCE", raising=False)
    kw = dict(venue="asterdex", equity=1000.0, fill_notional=100.0)
    res_hit = RP.replay_symbol("SOL", tick_series=_tick_series(True), **kw)
    assert res_hit.fills > 0, "逐笔打到 99.90 < bid 99.95 ⇒ 必须有成交"
    res_miss = RP.replay_symbol("SOL", tick_series=_tick_series(False), **kw)
    assert res_miss.fills == 0, "逐笔全在 99.99（未穿越 bid）⇒ 不得有成交"


@pytest.mark.unit
def test_replay_bucket_mode_untouched_without_env(monkeypatch):
    """不传 tick_series 且 env 未开 ⇒ 走桶路径（series 必填，否则 no_data）。"""
    monkeypatch.delenv("MM_SEG_SOURCE", raising=False)
    # 只传 series（桶形状）而不传 tick_series ⇒ 不允许静默回落到 tick
    ots = (np.arange(120) * 15000).astype(np.int64)
    series = (ots, np.full(120, 99.0), np.full(120, 101.0),
              (np.arange(60) * 30000 + 7000).astype(np.int64),
              np.full(60, 99.90), np.full(60, 99.90),
              np.full(60, 50.0), np.full(60, 50.0))
    res = RP.replay_symbol("SOL", venue="asterdex", equity=1000.0,
                           fill_notional=100.0, series=series)
    assert res.fills > 0, "桶路径行为必须保持（价格穿越 ⇒ 成交）"
