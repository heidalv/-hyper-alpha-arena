# -*- coding: utf-8 -*-
"""[F204 2026-09-15] 趋势**反向**偏斜与 `trend_blocked_side` 符号修正的回归测试。

为什么必须有这组测试：
账本实证（2915 笔、严格无未来函数）给出的是一个**与直觉相反**的结论：
    顺势成交（买在上涨 / 卖在下跌）−4.053bp ✗    逆势成交（买在下跌 / 卖在上涨）+2.109bp ✓
即"下跌中买入"是**赚钱**的那一侧，而旧代码 `trend_blocked_side` 恰好禁止它 ✗。
这类"符号"一旦写反，效果会从 +2bp 直接变成 −4bp ✗✗，而代码看起来完全正常 ⇒
必须用测试把**方向**钉死（而不是只测"函数被调用了"）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.market_maker.core import (  # noqa: E402
    QuoteParams, compute_quote, trend_blocked_side, trend_move_bp,
)

MID = 100.0


def _q(trend_bp: float, **kw):
    fields = dict(w_base_bp=10.0, k_inv=0.0, min_width_bp=0.0,
                  min_width_reduce_bp=0.0, frozen_width_bp=None)
    fields.update(kw)                      # 允许逐项覆盖（先合并再构造，避免重复关键字 ✗）
    p = QuoteParams(**fields)
    return compute_quote(symbol="BTC", mid=MID, sigma_norm=0.0, inv_ratio=0.0,
                         slow_range_bp=0.0, trend_bp=trend_bp, params=p)


def test_k_trend_zero_is_byte_identical_to_old_behaviour():
    """k_trend=0（默认）⇒ 与不传 trend_bp 完全一致（旧行为逐字保留 ✓）。"""
    a = _q(0.0)
    b = _q(123.0)          # 传了趋势但 k_trend=0 ⇒ 应当无任何影响
    assert a is not None and b is not None
    assert a.w_bid_bp == b.w_bid_bp and a.w_ask_bp == b.w_ask_bp
    assert a.w_bid_bp == 10.0 and a.w_ask_bp == 10.0


def test_uprise_widens_bid_and_tightens_ask():
    """**方向性命门**：上涨 ⇒ 买单挂远（更难追买）+ 卖单挂近（顺势出货）。

    注意断言口径：bid 永远在 mid 下方 ⇒ 不能说"bid > mid" ✗，要比**同一参数下的无偏斜值**
    （`trend_bp=0`，此时 k_trend 不起作用 ⇒ 就是 base ✓）。
    """
    base = _q(0.0, k_trend=1.0, trend_skew_scale_bp=20.0)
    q = _q(20.0, k_trend=1.0, trend_skew_scale_bp=20.0)
    assert base is not None and q is not None
    assert q.w_bid_bp > base.w_bid_bp, f"上涨时买单应挂更远：{q.w_bid_bp} vs {base.w_bid_bp}"
    assert q.w_ask_bp < base.w_ask_bp, f"上涨时卖单应挂更近：{q.w_ask_bp} vs {base.w_ask_bp}"
    # 双边整体下移 = 顺势出货
    assert q.bid < base.bid and q.ask < base.ask


def test_downmove_is_exact_mirror():
    """下跌 ⇒ 完全镜像（买单挂近=低吸 ✓、卖单挂远=别杀跌 ✓），整体上移。"""
    up = _q(20.0, k_trend=1.0)
    dn = _q(-20.0, k_trend=1.0)
    assert up is not None and dn is not None
    assert abs(dn.w_bid_bp - up.w_ask_bp) < 1e-9
    assert abs(dn.w_ask_bp - up.w_bid_bp) < 1e-9
    assert dn.bid > up.bid and dn.ask > up.ask


def test_skew_is_bounded_and_never_negative_width():
    """极端趋势下挂宽不得变成 0/负（偏斜系数有 0.05 下界 ⇒ 任何 k_trend 都不会翻转符号）。

    地板语义提醒：`bid_floor/ask_floor` 是**按库存方向分侧**取的
    （`min_width_reduce_bp` 只在该侧是"减仓侧"时生效，否则用 `min_width_bp`），
    所以这里对 flat 库存断言两侧都 ≥ `min_width_bp` ✓。
    """
    q = _q(10_000.0, k_trend=1.0, min_width_bp=1.0, min_width_reduce_bp=2.0)
    assert q is not None
    assert q.w_bid_bp >= 1.0 and q.w_ask_bp >= 1.0
    assert q.w_bid_bp <= 60.0 and q.w_ask_bp <= 60.0
    # 反向极端同样不得为负
    q2 = _q(-10_000.0, k_trend=1.0, min_width_bp=1.0, min_width_reduce_bp=2.0)
    assert q2 is not None and q2.w_bid_bp >= 1.0 and q2.w_ask_bp >= 1.0


def test_skew_composes_with_inventory_skew():
    """与库存偏斜可叠加（两个独立维度，互不吞掉）。"""
    p = dict(k_trend=1.0, trend_skew_scale_bp=20.0)
    flat = _q(20.0, k_inv=0.0, **p)
    long_inv = compute_quote(symbol="BTC", mid=MID, sigma_norm=0.0, inv_ratio=0.5,
                             slow_range_bp=0.0, trend_bp=20.0,
                             params=QuoteParams(w_base_bp=10.0, k_inv=1.0,
                                                min_width_bp=0.0, min_width_reduce_bp=0.0,
                                                frozen_width_bp=None,
                                                k_trend=1.0, trend_skew_scale_bp=20.0))
    assert flat is not None and long_inv is not None
    # 多头库存 ⇒ 卖侧更近（鼓励减仓）叠加在"上涨⇒卖侧更近"之上 ⇒ 应更近
    assert long_inv.w_ask_bp < flat.w_ask_bp
    # 买侧：多头库存使其更远，上涨也使其更远 ⇒ 更远
    assert long_inv.w_bid_bp > flat.w_bid_bp


def test_trend_blocked_side_now_blocks_the_with_trend_side():
    """**符号修正**：涨→禁买、跌→禁卖（原来正好相反 ✗）。"""
    rising = [100.0 + i * 0.05 for i in range(40)]      # 净上涨
    falling = [100.0 - i * 0.05 for i in range(40)]     # 净下跌
    assert trend_move_bp(rising, 20) > 0
    assert trend_move_bp(falling, 20) < 0
    assert trend_blocked_side(rising, 3.0, 20) == "buy", "上涨应禁买（原来返回 sell ✗）"
    assert trend_blocked_side(falling, 3.0, 20) == "sell", "下跌应禁卖（原来返回 buy ✗）"
    # 关闭态不受影响
    assert trend_blocked_side(rising, 0.0, 20) == ""
    assert trend_blocked_side(falling, -1.0, 20) == ""
    # 趋势不足阈值 ⇒ 不拦
    assert trend_blocked_side([100.0, 100.001, 100.0], 50.0, 3) == ""
