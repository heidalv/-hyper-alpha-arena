# -*- coding: utf-8 -*-
"""[F280] 价差相对挂宽 + 绝不穿越 —— 单测。

背景（H53b/H54/H55 实测）：
  · Aster 32 个币的价差 p50 从 **0.0124bp（BTC）到 20.19bp（VIRTUAL）**，跨度 **1629 倍**；
  · 引擎旧口径是**绝对 bp** 挂宽，实盘 `avg_width_bp=1.425`：
      BTC 半价差 0.0062bp ⇒ **229.7 倍**（永远打不到）
      SEI 半价差 9.47bp   ⇒ **0.2 倍**（报价已在价差内，被逆选择）
  · 且 δ 用绝对 bp 时 BTC 上报价落在卖一**上方 0.0376bp**，**穿越率 86.89%**
    —— 可成交单却按 maker 计费 ⇒ 虚增收益（H52 的 +0.39bp 就是这个伪影）。

本测试锁定三件事：
  1. `spread_mult<=0` ⇒ 与旧口径**逐字一致**（回归安全）；
  2. `spread_mult>0` ⇒ 挂宽 = spread_mult × 半价差（核心修复）；
  3. **绝不穿越**：任意极端输入下 `bid < best_bid?` 否 —— 必须 `best_bid <= bid < ask <= best_ask`。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker.core import QuoteParams, compute_quote  # noqa: E402


def test_old_behavior_unchanged():
    """spread_mult 默认 0.0 ⇒ 完全走旧的绝对 bp 路径（回归安全）。

    注意：报出的 `w_bid_bp` 是**钳制后**的实际宽度（F189 教训），
    所以这里断言的是**引擎算出的基准宽度** `base_bp` 未被 F280 触碰，
    以及 mode 仍是 "normal"（没有误入 spread 分支）。
    """
    p = QuoteParams(w_base_bp=1.425, min_width_bp=3.0, k_vol=0.0, k_inv=1.0, k_trend=0.0)
    q = compute_quote(symbol="BTCUSDT", mid=80855.0, params=p,
                      spread_bp=0.0124, best_bid=80854.9, best_ask=80855.0)
    assert q is not None
    # 旧口径：base = w_base_bp*(1+k_vol*0) = 1.425，mode = normal（未进 spread 分支）
    assert abs(q.base_bp - 1.425) < 1e-6, q.base_bp
    assert q.mode == "normal", q.mode
    # 旧口径的 3.0bp 下限把报价推到盘口外 3bp —— 那正是 F280 要修的病灶。
    # 有了盘口后不穿越钳制把 bid 拉回买一（= 真实可挂的最优价），这是**新增的物理约束**。
    assert q.bid <= q.mid <= q.ask
    assert q.bid >= 80854.9 - 1e-9, "bid 被钳到买一之下（越过了买一）"
    assert q.ask <= 80855.0 + 1e-9, "ask 被钳到卖一之上（穿越了卖一）"


def test_old_behavior_no_book_unclamped():
    """不给盘口 ⇒ 不钳制；旧口径的宽度应原样生效（证明旧路径未被污染）。"""
    p = QuoteParams(w_base_bp=1.425, min_width_bp=3.0, k_vol=0.0, k_inv=1.0, k_trend=0.0)
    mid = 80855.0
    q = compute_quote(symbol="BTCUSDT", mid=mid, params=p)
    assert q is not None
    assert abs(q.w_bid_bp - 3.0) < 1e-6, q.w_bid_bp
    assert abs(q.bid - mid * (1 - 3.0 / 1e4)) < 1e-6


def test_spread_relative_width():
    """spread_mult>0 ⇒ 挂宽 = spread_mult × 半价差。"""
    p = QuoteParams(spread_mult=0.9, spread_cross_margin=0.05, k_vol=0.0, k_inv=0.0)
    # SOL：价差 0.92bp ⇒ 半价差 0.46bp ⇒ 0.9×0.46 = 0.414bp
    q = compute_quote(symbol="SOLUSDT", mid=108.475, spread_bp=0.92,
                      best_bid=108.47, best_ask=108.48, params=p)
    assert q is not None and q.mode == "spread", q.mode
    assert abs(q.base_bp - 0.9 * 0.46) < 1e-6, q.base_bp
    assert abs(q.w_bid_bp - 0.9 * 0.46) < 1e-6, q.w_bid_bp


def test_spread_relative_scales_with_spread():
    """同一个 spread_mult 在不同价差下给出**按比例**的宽度 —— 这就是 1629 倍跨度的解。"""
    p = QuoteParams(spread_mult=0.9, spread_cross_margin=0.05, k_vol=0.0, k_inv=0.0)
    narrow = compute_quote(symbol="BTCUSDT", mid=80855.0, spread_bp=0.0124,
                           best_bid=80854.9, best_ask=80855.0, params=p)
    wide = compute_quote(symbol="VIRTUALUSDT", mid=1.0, spread_bp=20.19,
                         best_bid=0.99899, best_ask=1.00101, params=p)
    assert narrow is not None and wide is not None
    ratio = wide.base_bp / narrow.base_bp
    # base_bp 四舍五入到 4 位小数 ⇒ 近零价差那侧有 ~4% 量化误差
    assert abs(ratio - 20.19 / 0.0124) / (20.19 / 0.0124) < 0.05, ratio
    assert ratio > 1000, "宽价差币的挂宽必须同步放大"


def test_never_cross_buy():
    """**核心不变量**：报价绝不穿越对侧最优价，任意极端 spread_mult 下。"""
    for sm in (0.9, 1.0, 5.0, 100.0):
        p = QuoteParams(spread_mult=sm, spread_cross_margin=0.05, k_vol=0.0, k_inv=0.0)
        bb, ba = 80854.9, 80855.0
        q = compute_quote(symbol="BTCUSDT", mid=0.5 * (bb + ba), spread_bp=(ba - bb) / 80854.95 * 1e4,
                          best_bid=bb, best_ask=ba, params=p)
        assert q is not None
        assert q.bid <= q.ask, (sm, q.bid, q.ask)
        # 进价差内可以，但**不得 >= 卖一**（那就是可成交单）
        assert q.bid < ba, f"spread_mult={sm} 时 bid={q.bid} 穿越了卖一 {ba}"
        assert q.ask > bb, f"spread_mult={sm} 时 ask={q.ask} 穿越了买一 {bb}"


def test_never_cross_with_skew():
    """库存/趋势偏斜把一侧推远时，仍不得穿越。"""
    for inv in (-1.0, -0.5, 0.0, 0.5, 1.0):
        p = QuoteParams(spread_mult=1.0, spread_cross_margin=0.05, k_vol=0.0,
                        k_inv=1.0, k_trend=1.0, trend_skew_scale_bp=20.0)
        bb, ba = 80854.9, 80855.0
        q = compute_quote(symbol="BTCUSDT", mid=0.5 * (bb + ba),
                          spread_bp=(ba - bb) / 80854.95 * 1e4,
                          best_bid=bb, best_ask=ba, inv_ratio=inv, trend_bp=40.0, params=p)
        assert q is not None
        assert q.bid < ba and q.ask > bb, (inv, q.bid, q.ask)
        assert q.bid <= q.ask


def test_extreme_degenerate_book():
    """退化盘口（bid>=ask 或 0）不得抛异常，且必须保持 bid<mid<ask。"""
    p = QuoteParams(spread_mult=0.9, k_vol=0.0, k_inv=0.0)
    q = compute_quote(symbol="X", mid=100.0, spread_bp=0.0,
                      best_bid=0.0, best_ask=0.0, params=p)
    assert q is not None
    assert q.bid < q.mid < q.ask, (q.bid, q.mid, q.ask)
    # spread_bp 无效 ⇒ 退回绝对 bp 路径，不得崩
    q2 = compute_quote(symbol="X", mid=100.0, spread_bp=5.0, best_bid=101.0, best_ask=100.0, params=p)
    assert q2 is not None and q2.bid < q2.ask


def test_live_width_records_post_clamp_value():
    """宽度必须报**钳制后**的实际值（F189 教训：模型报 12bp、实盘挂 5.5bp）。"""
    p = QuoteParams(w_base_bp=50.0, min_width_bp=3.0, k_vol=0.0, k_inv=0.0)
    bb, ba = 80854.9, 80855.0
    q = compute_quote(symbol="BTCUSDT", mid=0.5 * (bb + ba), best_bid=bb, best_ask=ba, params=p)
    assert q is not None
    # 算出来的 50bp 被钳到盘口内 ⇒ 实际宽度应远小于 50bp
    assert q.w_bid_bp < 0.01, q.w_bid_bp
    # 且报出的宽度必须与报出的价格自洽
    assert abs(q.bid - q.mid * (1 - q.w_bid_bp / 1e4)) < 1e-6 * q.mid
