# -*- coding: utf-8 -*-
"""[F272 2026-09-16 · E2] microprice 偏离闸契约（选择性成交）：

  microprice 高于 mid（买压 ⇒ 价大概率上行）⇒ 封**卖**（挂卖会被抬起后继续涨）；
  microprice 低于 mid（卖压 ⇒ 价大概率下行）⇒ 封**买**（挂买会被砸中后继续跌）；
  减仓侧豁免（空头遇买压封买 ⇒ 放行；多头遇卖压封卖 ⇒ 放行）；
  阈值 0 / 偏离小于阈值 ⇒ 旧行为逐字一致。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import InventoryBook, LaneRiskLimits, QuoteParams  # noqa: E402
from backend.services.market_maker.runner import SymbolState, plan_tick  # noqa: E402


def _limits(mp_bp: float = 3.0):
    return LaneRiskLimits(mp_block_bp=mp_bp, ofi_block_threshold=0.0,
                          vol_pause_sigma=0.0, trend_pause_bp=0.0,
                          stop_loss_bp=0.0, max_one_side_seconds=3600.0,
                          max_net_directional_ratio=0.3, max_net_exposure_ratio=0.6,
                          max_gross_notional_ratio=1.0)


def _tick(mp_skew_bp: float, limits, qty: float = 0.0):
    st = SymbolState(symbol="BTC", qty=qty,
                     avg_px=100.0, avg_mid=100.0, opened_ts=time.time() - 10.0)
    book = InventoryBook()
    if qty:
        book.apply_fill(symbol="BTC", side="buy" if qty > 0 else "sell",
                        qty=abs(qty), fill_px=100.0, mid_px=100.0, now_ts=time.time())
    dec, _ = plan_tick(
        state=st, mid=100.0, seg_low=0.0, seg_high=0.0,
        seg_taker_sell=0.0, seg_taker_buy=0.0, now_ts=time.time(),
        params=QuoteParams(w_base_bp=10.0, k_inv=0.5, frozen_width_bp=None),
        limits=limits, equity=300.0, fill_notional=30.0,
        taker_fee_bp=4.0, maker_fee_bp=0.0, half_spread=0.05,
        sigma_norm=0.0, book=book, marks={"BTC": 100.0},
        ofi=0.0, day_pnl_usd=0.0, mp_skew_bp=mp_skew_bp,
        pending={"up": 0.0, "down": 0.0, "gross": 0.0},
        lane_limits_enforce=True)
    return st, dec


def test_positive_skew_blocks_sell():
    """买压（mp 高于 mid）⇒ 封卖；买侧仍挂。"""
    _, dec = _tick(mp_skew_bp=+5.0, limits=_limits())
    assert dec.ask == 0.0, "买压下必须封卖"
    assert dec.bid > 0.0, "买侧应照常挂"
    assert dec.skip == "mp_skew_sell", dec.skip


def test_negative_skew_blocks_buy():
    """卖压（mp 低于 mid）⇒ 封买；卖侧仍挂。"""
    _, dec = _tick(mp_skew_bp=-5.0, limits=_limits())
    assert dec.bid == 0.0, "卖压下必须封买"
    assert dec.ask > 0.0, "卖侧应照常挂"
    assert dec.skip == "mp_skew_buy", dec.skip


def test_below_threshold_is_unchanged():
    """偏离小于阈值 ⇒ 双边照常挂（旧行为）。"""
    _, dec = _tick(mp_skew_bp=+1.0, limits=_limits(mp_bp=3.0))
    assert dec.bid > 0.0 and dec.ask > 0.0, "阈值内不得干预"


def test_disabled_is_unchanged():
    """阈值 0（关闭）⇒ 旧行为逐字一致。"""
    _, dec = _tick(mp_skew_bp=+50.0, limits=_limits(mp_bp=0.0))
    assert dec.bid > 0.0 and dec.ask > 0.0


def test_reduce_side_exempt():
    """减仓侧豁免：空头 + 买压（封买）⇒ 放行（买是空头的减仓侧）。"""
    _, dec = _tick(mp_skew_bp=+5.0, limits=_limits(), qty=-1.0)
    # 买压封卖；空头持仓（qty<0）时"卖"是加仓侧 ⇒ 仍封卖，买侧放行
    assert dec.bid > 0.0
    assert dec.ask == 0.0
    _, dec2 = _tick(mp_skew_bp=-5.0, limits=_limits(), qty=-1.0)
    # 卖压封买；空头的减仓侧 = 买 ⇒ 豁免 ⇒ 买侧仍挂
    assert dec2.bid > 0.0, "空头的减仓侧（买）必须豁免"


# ── [h397 2026-09-27] #9 微价计算与实盘接线契约 ─────────────────────────

def test_microprice_skew_sell_pressure():
    """卖压（ask 量重）⇒ mp 低于 mid ⇒ 负偏移。"""
    from backend.services.market_maker.core import microprice_skew_bp
    skew = microprice_skew_bp([[99.9, 10.0]], [[100.1, 90.0]])
    assert abs(skew - (-8.0)) < 0.01, f"期望 −8bp，实际 {skew}"


def test_microprice_skew_buy_pressure():
    """买压（bid 量重）⇒ mp 高于 mid ⇒ 正偏移。"""
    from backend.services.market_maker.core import microprice_skew_bp
    skew = microprice_skew_bp([[99.9, 90.0]], [[100.1, 10.0]])
    assert abs(skew - 8.0) < 0.01, f"期望 +8bp，实际 {skew}"


def test_microprice_skew_bad_input_zero():
    """空/坏输入 ⇒ 0.0（闸不干预）。"""
    from backend.services.market_maker.core import microprice_skew_bp
    assert microprice_skew_bp([], []) == 0.0
    assert microprice_skew_bp(None, None) == 0.0
    assert microprice_skew_bp([[0.0, 1.0]], [[1.0, 1.0]]) == 0.0


def test_mp_skew_live_wiring_source_contract():
    """接线契约（F272 曾实现但实盘从未喂输入 ⇒ 死闸）：
    fetch_market 必须刷新微价并放进市场字典，tick 必须把 mp_skew 传进 plan_tick。"""
    import inspect

    from backend.services.market_maker.runner import ShadowRunner
    src_fm = inspect.getsource(ShadowRunner.fetch_market)
    assert "_refresh_microprice" in src_fm, "fetch_market 必须刷新微价缓存"
    assert '"mp_skew"' in src_fm, "市场字典必须携带 mp_skew"
    src_tick = inspect.getsource(ShadowRunner.tick)
    assert "mp_skew_bp=" in src_tick, "tick 必须把 mp_skew 传给 plan_tick"
