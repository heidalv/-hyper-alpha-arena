# -*- coding: utf-8 -*-
"""[h432 2026-09-28] v2 出场架构：波动张开价差（vol_spread_k）+ 减仓侧偏斜消融（exit_skew_k）。

契约：
  · 两个新参数默认 0 ⇒ 报价与旧版**逐字一致**（回归护栏）；
  · vol_spread_k：spread 模式下 smult_eff = smult × (1 + k × σ_capped)；
  · exit_skew_k：adverse_bp ≥ scale 且 k=1 时减仓侧宽度 → 0（挂 mid 被动出库），
    加仓侧不受影响；adverse≤0 / 无偏移 ⇒ 不变。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.market_maker.core import QuoteParams, compute_quote  # noqa: E402


def _q(**kw):
    """spread 模式基准：半价差 2bp（spread_bp=4）、mid=100、无盘口钳制。"""
    fields = dict(spread_mult=0.5, spread_mult_reduce=0.4, spread_cross_margin=0.05)
    fields.update(kw)
    return compute_quote(symbol="X", mid=100.0, sigma_norm=0.0, inv_ratio=0.0,
                         spread_bp=4.0, params=QuoteParams(**fields))


def test_v2_params_default_off_byte_identical():
    """k=0 默认：报价与旧版逐字一致（w_bid/w_ask = smult × 半价差）。"""
    q = _q()
    assert q is not None
    assert abs(q.w_bid_bp - 1.0) < 1e-9, q.w_bid_bp   # 0.5 × 2bp 半价差
    assert abs(q.w_ask_bp - 1.0) < 1e-9, q.w_ask_bp


def test_vol_spread_k_widens_with_sigma():
    """σ_norm=2、k=1 ⇒ smult_eff = 0.5×(1+2)=1.5 ⇒ 半宽 3bp。"""
    q = _q(vol_spread_k=1.0)
    q2 = compute_quote(symbol="X", mid=100.0, sigma_norm=2.0, inv_ratio=0.0,
                       spread_bp=4.0,
                       params=QuoteParams(spread_mult=0.5, spread_mult_reduce=0.4,
                                          vol_spread_k=1.0, k_vol_sigma_cap=0.0))
    assert q is not None and abs(q.w_bid_bp - 1.0) < 1e-9      # σ=0 ⇒ 不变
    assert q2 is not None and abs(q2.w_bid_bp - 3.0) < 1e-9, q2.w_bid_bp  # 0.5×3×2


def test_vol_spread_k_respects_sigma_cap():
    """σ_cap=1、σ=2 ⇒ σ_capped=1 ⇒ smult_eff = 0.5×2=1.0 ⇒ 半宽 2bp。"""
    q = compute_quote(symbol="X", mid=100.0, sigma_norm=2.0, inv_ratio=0.0,
                      spread_bp=4.0,
                      params=QuoteParams(spread_mult=0.5, spread_mult_reduce=0.4,
                                         vol_spread_k=1.0, k_vol_sigma_cap=1.0))
    assert q is not None and abs(q.w_bid_bp - 2.0) < 1e-9, q.w_bid_bp


def test_exit_skew_tightens_reduce_side_only_long():
    """多头（inv>0）且水下 40bp、k=1 ⇒ 卖侧（减仓侧）宽度 → 0（挂 mid），买侧不变。
    （k_inv=0 隔离库存偏斜，只测 exit_skew 的效应。）"""
    q = _q(exit_skew_k=1.0)
    q2 = compute_quote(symbol="X", mid=100.0, sigma_norm=0.0, inv_ratio=0.5,
                       spread_bp=4.0, adverse_bp=40.0,
                       params=QuoteParams(spread_mult=0.5, spread_mult_reduce=0.4,
                                          k_inv=0.0,
                                          exit_skew_k=1.0, exit_skew_scale_bp=40.0))
    assert q is not None and abs(q.w_ask_bp - 1.0) < 1e-9          # 无偏移 ⇒ 不变
    assert q2 is not None
    assert q2.w_ask_bp < 1e-6, q2.w_ask_bp      # 减仓侧（ask）被偏斜到 0
    # 加仓侧（bid）不受 exit_skew 影响：0.5×2bp=1bp
    assert abs(q2.w_bid_bp - 1.0) < 1e-9, q2.w_bid_bp


def test_exit_skew_short_side():
    """空头（inv<0）水下 ⇒ 买侧（减仓侧）宽度 → 0。"""
    q = compute_quote(symbol="X", mid=100.0, sigma_norm=0.0, inv_ratio=-0.5,
                      spread_bp=4.0, adverse_bp=40.0,
                      params=QuoteParams(spread_mult=0.5, spread_mult_reduce=0.4,
                                         k_inv=0.0,
                                         exit_skew_k=1.0, exit_skew_scale_bp=40.0))
    assert q is not None and q.w_bid_bp < 1e-6, q.w_bid_bp
    assert abs(q.w_ask_bp - 1.0) < 1e-9, q.w_ask_bp


def test_exit_skew_scales_linearly():
    """adverse=20（scale 40 的一半）、k=1 ⇒ 因子 0.5 ⇒ 减仓侧 0.4×2×0.5=0.4bp。"""
    q = compute_quote(symbol="X", mid=100.0, sigma_norm=0.0, inv_ratio=0.5,
                      spread_bp=4.0, adverse_bp=20.0,
                      params=QuoteParams(spread_mult=0.5, spread_mult_reduce=0.4,
                                         k_inv=0.0,
                                         exit_skew_k=1.0, exit_skew_scale_bp=40.0))
    assert q is not None
    assert abs(q.w_ask_bp - 0.4) < 1e-9, q.w_ask_bp


def test_exit_skew_no_adverse_no_effect():
    """adverse=0 ⇒ 即使 k=1 也逐字不变。"""
    q = compute_quote(symbol="X", mid=100.0, sigma_norm=0.0, inv_ratio=0.5,
                      spread_bp=4.0, adverse_bp=0.0,
                      params=QuoteParams(spread_mult=0.5, spread_mult_reduce=0.4,
                                         exit_skew_k=1.0, exit_skew_scale_bp=40.0))
    assert q is not None
    assert abs(q.w_ask_bp - 0.4) < 1e-9, q.w_ask_bp   # 0.4×2bp（k_inv=0）
