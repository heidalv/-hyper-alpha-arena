# -*- coding: utf-8 -*-
"""[F286] 减仓侧独立价差倍数（出库腿挂 mid）—— 单测。

依据（H77，24h、21,527 笔入场、判据已过极端情形单调性测试）：

    出库报价 = ask − f×价差 时的**整往返**每笔净额（含强平，分母=全部入场）
      f=0.00（挂 best_ask）  hold60s −1.7624bp   成交率 78.8%
      f=0.50                hold60s −1.7795bp   成交率 80.9%
      f=1.00（挂 mid）       hold60s **−0.8398bp**  成交率 **93.3%**
    ⇒ 出库挂 mid 比挂 best_ask 好 **+0.92bp**；机制自洽（半价差≈0.9bp）。

引擎当前出库用 `spread_mult=0.9` ⇒ 报价在 `bid+0.9h` ≈ 0.45×价差（对应 f≈0.5，非最优）。
本参数让**减仓侧**单独用 `spread_mult_reduce=1.0`（挂 mid），
**进场侧保持 0.9 不变**（H56/H77 已确认 0.9 合适）。

锁定的性质：
  1. `spread_mult_reduce<=0` ⇒ 与 `spread_mult` 完全相同（回归安全）
  2. 多头库存（inv_ratio>0）时**卖侧**用减仓宽度、买侧用正常宽度
  3. 空头库存（inv_ratio<0）时**买侧**用减仓宽度
  4. 减仓侧宽度 ≈ `spread_mult_reduce × 半价差`
  5. 仍不穿越对侧最优价
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import QuoteParams, compute_quote  # noqa: E402

BB, BA = 80854.9, 80855.0
SP = (BA - BB) / 80854.95 * 1e4     # ≈ 0.0124bp（BTC 实测）
MID = 0.5 * (BB + BA)


def _q(*, inv_ratio=0.0, mid=MID, bb=BB, ba=BA, sp=None, **pkw):
    """把 QuoteParams 参数与 compute_quote 参数分开传，避免混用。

    ⚠️ 必须显式 `w_base_bp=0`：否则旧的绝对 bp 基准（默认 5.0）会主导
    `base`，把价差相对宽度掩盖掉（这正是 F280 的病根）。
    """
    pkw.setdefault("w_base_bp", 0.0)
    pkw.setdefault("k_vol", 0.0)
    pkw.setdefault("k_inv", 0.0)
    pkw.setdefault("k_trend", 0.0)
    p = QuoteParams(**pkw)
    if sp is None:
        sp = (ba - bb) / (0.5 * (bb + ba)) * 1e4
    q = compute_quote(symbol="TESTUSDT", mid=mid, spread_bp=sp,
                      best_bid=bb, best_ask=ba, inv_ratio=inv_ratio, params=p)
    return q, p


def test_reduce_zero_falls_back_to_spread_mult():
    """spread_mult_reduce<=0 ⇒ 与只用 spread_mult 完全一致（回归安全）。"""
    a, _ = _q(spread_mult=0.9, spread_mult_reduce=0.0)
    b, _ = _q(spread_mult=0.9)
    assert abs(a.base_bp - b.base_bp) < 1e-12
    assert abs(a.w_bid_bp - b.w_bid_bp) < 1e-12
    assert abs(a.w_ask_bp - b.w_ask_bp) < 1e-12


def test_long_inventory_sell_side_uses_reduce():
    """多头库存 ⇒ 卖侧（减仓）用 reduce 宽度，买侧用正常宽度。

    实测（价差 1.9998bp ⇒ 半价差 0.9999bp）：
      inv=+0.8 red=1.0 -> w_bid=0.899910（= 0.9×half）  w_ask=0.999900（= 1.0×half）
    """
    bb, ba = 100.0, 100.02
    mid = 0.5 * (bb + ba)
    half = (ba - bb) / mid * 1e4 / 2.0      # ≈ 0.9999bp
    q, _ = _q(inv_ratio=0.8, mid=mid, bb=bb, ba=ba,
              spread_mult=0.9, spread_mult_reduce=1.0)
    assert q.w_ask_bp > q.w_bid_bp, (
        f"多头库存下卖侧应用 reduce(更大宽度)：w_ask={q.w_ask_bp} w_bid={q.w_bid_bp}")
    assert abs(q.w_ask_bp - 1.0 * half) < 1e-6, q.w_ask_bp
    assert abs(q.w_bid_bp - 0.9 * half) < 1e-6, q.w_bid_bp
    assert q.bid <= ba and q.ask >= bb and q.bid <= q.ask


def test_short_inventory_buy_side_uses_reduce():
    """空头库存 ⇒ 买侧（减仓）用 reduce 宽度。"""
    bb, ba = 100.0, 100.02
    mid = 0.5 * (bb + ba)
    half = (ba - bb) / mid * 1e4 / 2.0
    q, _ = _q(inv_ratio=-0.8, mid=mid, bb=bb, ba=ba,
              spread_mult=0.9, spread_mult_reduce=1.0)
    assert q.w_bid_bp > q.w_ask_bp, (
        f"空头库存下买侧应用 reduce：w_bid={q.w_bid_bp} w_ask={q.w_ask_bp}")
    assert abs(q.w_bid_bp - 1.0 * half) < 1e-6, q.w_bid_bp
    assert abs(q.w_ask_bp - 0.9 * half) < 1e-6, q.w_ask_bp
    assert q.bid <= ba and q.ask >= bb


def test_flat_inventory_ignores_reduce():
    """无库存（inv=0）⇒ 两侧都用 `spread_mult`，reduce 不参与。"""
    bb, ba = 100.0, 100.02
    mid = 0.5 * (bb + ba)
    half = (ba - bb) / mid * 1e4 / 2.0
    q, _ = _q(inv_ratio=0.0, mid=mid, bb=bb, ba=ba,
              spread_mult=0.9, spread_mult_reduce=1.0)
    assert abs(q.w_bid_bp - 0.9 * half) < 1e-6, q.w_bid_bp
    assert abs(q.w_ask_bp - 0.9 * half) < 1e-6, q.w_ask_bp


def test_reduce_one_places_exit_at_mid():
    """reduce=1.0 ⇒ 减仓侧**目标宽度** = 1.0 × 半价差（即正好落在 mid）。

    ⚠️ 不能用 `ask` 去断言：`ask` 还被 **F280 不穿越钳制**夹在 `best_ask` 之内
    （`best_ask=100.02`，而 `mid=100.0099…` ⇒ 钳制后 `ask=100.02`，距 mid 0.9999bp），
    那是钳制的结果，不是宽度算错。所以这里断言"目标宽度"，并把钳制显式写出来。
    """
    bb, ba = 100.0, 100.02
    mid = 0.5 * (bb + ba)
    half = (ba - bb) / mid * 1e4 / 2.0
    q, _ = _q(inv_ratio=0.9, mid=mid, bb=bb, ba=ba,
              spread_mult=0.9, spread_mult_reduce=1.0)
    # 减仓侧（多头 ⇒ 卖侧）目标宽度 = 1.0 × 半价差
    assert abs(q.base_bp - 1.0 * half) < 1e-6, q.base_bp
    # 且报价被钳制在盘口内（不穿越）
    assert q.ask <= ba + 1e-12, (q.ask, ba)
    assert q.bid >= bb - 1e-12, (q.bid, bb)
