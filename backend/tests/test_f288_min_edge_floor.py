# -*- coding: utf-8 -*-
"""[F288] 最小挂单距离下限（min_edge_frac）—— 单测。

依据（H88 实盘 561 个真实周期；H89 排除币种混杂）：

    edge_bp 区间        周期   强平率    每周期真实净额
      (-inf, 0.1071)    112   64.3%    **−0.08610**  ← 最差
      [0.1071, 0.3202)  109   10.1%    **+0.00410**  ← 最好
      [0.6413, inf)     114   25.4%     −0.03165
    最好−最差 = +0.09020 USD/周期（3.21 SE，显著）

H89 混杂检验：最差档最大单一币占比仅 33.0%；**币内 6/6 同向** ⇒ edge 是独立因素。

语义：`edge >= min_edge_frac × 半价差`。`0` = 关闭（旧行为）。

锁定的性质：
  1. `min_edge_frac=0` ⇒ 与不设下限**完全一致**（回归安全）
  2. 设了下限后，窄价差币的宽度被抬到 `下限 × 半价差`
  3. 宽价差币（原宽度已超下限）**不受影响**
  4. 下限与 `spread_mult` 取**较大者**，不是相加
  5. 仍不穿越对侧最优价
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import QuoteParams, compute_quote  # noqa: E402


def _q(*, mid=100.01, bb=100.0, ba=100.02, inv=0.0, **pkw):
    pkw.setdefault("w_base_bp", 0.0)
    pkw.setdefault("k_vol", 0.0)
    pkw.setdefault("k_inv", 0.0)
    pkw.setdefault("k_trend", 0.0)
    p = QuoteParams(**pkw)
    sp = (ba - bb) / (0.5 * (bb + ba)) * 1e4
    return compute_quote(symbol="T", mid=mid, spread_bp=sp, best_bid=bb,
                         best_ask=ba, inv_ratio=inv, params=p), sp


def test_zero_floor_is_identical():
    """min_edge_frac=0 ⇒ 与旧行为逐字一致（回归安全）。"""
    a, _ = _q(spread_mult=0.9, min_edge_frac=0.0)
    b, _ = _q(spread_mult=0.9)
    assert abs(a.w_bid_bp - b.w_bid_bp) < 1e-12
    assert abs(a.w_ask_bp - b.w_ask_bp) < 1e-12
    assert abs(a.base_bp - b.base_bp) < 1e-12


def test_floor_raises_narrow_quote():
    """下限高于 spread_mult 产出时，宽度被抬到下限。"""
    bb, ba = 100.0, 100.02          # 半价差 0.9999bp
    half = (ba - bb) / (0.5 * (bb + ba)) * 1e4 / 2.0
    q, _ = _q(mid=0.5 * (bb + ba), bb=bb, ba=ba,
              spread_mult=0.9, min_edge_frac=0.5)
    # 0.9×half=0.9bp 已 > 0.5×half=0.5bp ⇒ 不受影响（下限取 max）
    assert abs(q.base_bp - 0.9 * half) < 5e-4, q.base_bp

    q2, _ = _q(mid=0.5 * (bb + ba), bb=bb, ba=ba,
               spread_mult=0.2, min_edge_frac=1.0)
    # 0.2×half=0.2bp < 1.0×half ⇒ 抬到 1.0×half
    assert abs(q2.base_bp - 1.0 * half) < 5e-4, q2.base_bp


def test_floor_is_max_not_sum():
    """下限与 spread_mult 取 max，不相加。"""
    bb, ba = 100.0, 100.02
    half = (ba - bb) / (0.5 * (bb + ba)) * 1e4 / 2.0
    q, _ = _q(mid=0.5 * (bb + ba), bb=bb, ba=ba,
              spread_mult=0.3, min_edge_frac=1.5)
    assert abs(q.base_bp - 1.5 * half) < 5e-4, q.base_bp   # 不是 1.8×half


def test_wide_spread_coin_unaffected():
    """宽价差币原宽度已超下限 ⇒ 不受影响。"""
    bb, ba = 100.0, 100.20          # 20bp 价差
    half = (ba - bb) / (0.5 * (bb + ba)) * 1e4 / 2.0
    q0, _ = _q(mid=0.5 * (bb + ba), bb=bb, ba=ba, spread_mult=0.9, min_edge_frac=0.0)
    q1, _ = _q(mid=0.5 * (bb + ba), bb=bb, ba=ba, spread_mult=0.9, min_edge_frac=0.5)
    assert abs(q0.base_bp - q1.base_bp) < 5e-4, (q0.base_bp, q1.base_bp)
    assert abs(q1.base_bp - 0.9 * half) < 5e-4


def test_floor_never_crosses():
    """设了下限也不得穿越对侧最优价。"""
    bb, ba = 100.0, 100.02
    for mef in (0.0, 0.5, 1.0, 3.0, 10.0):
        q, _ = _q(mid=0.5 * (bb + ba), bb=bb, ba=ba,
                  spread_mult=0.9, min_edge_frac=mef)
        assert q.bid < ba and q.ask > bb, (mef, q.bid, q.ask)
        assert q.bid <= q.ask


def test_floor_not_applied_without_spread_mode():
    """非价差相对模式（spread_mult=0）⇒ 下限不参与，走旧绝对 bp 路径。"""
    p = QuoteParams(w_base_bp=2.0, min_width_bp=0.3, k_vol=0.0, k_inv=0.0,
                    spread_mult=0.0, min_edge_frac=5.0)
    # ⚠️ 不传盘口 ⇒ 不触发"不穿越钳制"，才能看到绝对 bp 原值
    q = compute_quote(symbol="T", mid=100.01, spread_bp=2.0, params=p)
    assert abs(q.w_bid_bp - 2.0) < 5e-4, q.w_bid_bp
    assert abs(q.w_ask_bp - 2.0) < 5e-4, q.w_ask_bp
