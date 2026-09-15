# -*- coding: utf-8 -*-
"""[F205 2026-09-15] 报价分支（normal/frozen）与基准半宽的**可观测性**契约。

为什么必须有这组测试：F189 的部署失败不是参数选错，而是**参数根本没生效** ✗✗ ——
模型（回放）里 `w_base_bp=12` 让半宽到 ~13bp，而实盘因 `frozen_width_bp=3.0` 的冻结档
实际只挂 **5.5/4.7bp**；我当时只能从"平均挂宽 vs 基线"反推，没有任何直接读数 ✗。
现在 `Quote.mode/base_bp` 把这个分支显式带出来，并进入 `/shadow` 的
`quote_modes / frozen_share / avg_base_bp` ✓ ⇒ "参数→行为"当场可核对 ✓。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))))

from backend.services.market_maker.core import QuoteParams, compute_quote  # noqa: E402


def _q(slow_range_bp: float, **kw):
    fields = dict(w_base_bp=10.0, k_vol=0.5, frozen_width_bp=3.0, frozen_max_move_bp=8.0)
    fields.update(kw)
    return compute_quote(symbol="BTC", mid=100.0, sigma_norm=0.0, inv_ratio=0.0,
                         slow_range_bp=slow_range_bp, params=QuoteParams(**fields))


def test_frozen_branch_is_labelled_and_bypasses_w_base():
    """安静行情（0 < 全幅 < 阈值）⇒ 走冻结档：mode=frozen 且 base 完全绕开 w_base ✓。"""
    q = _q(4.0)
    assert q is not None
    assert q.mode == "frozen"
    assert abs(q.base_bp - 3.0) < 1e-9, f"冻结档 base 应等于 frozen_width_bp，实际 {q.base_bp}"
    assert "mode=frozen" in q.reason
    # 关键：即使把 w_base 提到 12，冻结档下基准半宽**不变**（这正是 F189 的坑 ✗）
    q12 = _q(4.0, w_base_bp=12.0)
    assert q12 is not None and abs(q12.base_bp - 3.0) < 1e-9


def test_normal_branch_uses_w_base_and_sigma():
    """正常档：mode=normal，base = w_base×(1+k_vol×σ) ✓。"""
    q = _q(0.0)                      # slow_range_bp=0 ⇒ 不满足 0<x ⇒ 正常档
    assert q is not None and q.mode == "normal"
    assert abs(q.base_bp - 10.0) < 1e-9
    q2 = compute_quote(symbol="BTC", mid=100.0, sigma_norm=1.0, inv_ratio=0.0,
                       slow_range_bp=0.0,
                       params=QuoteParams(w_base_bp=10.0, k_vol=0.5,
                                          frozen_width_bp=3.0, frozen_max_move_bp=8.0))
    assert q2 is not None and abs(q2.base_bp - 15.0) < 1e-9      # 10×(1+0.5×1)


def test_range_at_or_above_threshold_is_normal():
    """全幅 ≥ 阈值 ⇒ 不是冻结（边界要严：等于阈值也算正常档）。"""
    for rng in (8.0, 9.0, 100.0):
        q = _q(rng)
        assert q is not None and q.mode == "normal", f"全幅 {rng} 不应判为冻结"


def test_frozen_disabled_means_always_normal():
    """`frozen_width_bp=None/0` = 关闭冻结档（旧行为）⇒ 永远 normal ✓。"""
    for fw in (None, 0.0):
        q = _q(1.0, frozen_width_bp=fw)
        assert q is not None and q.mode == "normal"
        assert abs(q.base_bp - 10.0) < 1e-9
