"""RR 门槛边界一致性 —— ScalpGate._ensure_min_rr 与 unified_gate 必须同口径。

[2026-09-02] 背景
-----------------
P2.2 把短线最低盈亏比抬到 2.0 后，ScalpGate 会把 TP 精确设成 SL×2.0，于是大量
订单恰好落在门槛上。上游 sl_pct 经 _adjust_sl_for_stop_hunt 从 sl_price 反推，
带浮点噪声；实测生产快照：

    tp_pct = 0.021291
    sl_pct = 0.010645500000000117
    tp/sl  = 1.9999999999999782

_ensure_min_rr 用 `rr + 1e-9 >= min_rr` 判"达标"不再抬 TP，unified_gate 用裸
`rr < min_rr` 判"不达标"拦单 —— 两层在边界上口径相反，近 2h 266 条短线快照
148 条（56%）死在 2e-14 的差上。

修复：两处共用 unified_gate.RR_EPS。本测试锁三件事：
  1. 生产实测值必须通过 gate 的 RR 判定；
  2. 对任意带噪声的 sl，经 _ensure_min_rr 处理后 gate 必放行（层间一致性）；
  3. RR_EPS 不能被悄悄放大成实质性的门槛松动。
"""
from __future__ import annotations

import random

import pytest

from backend.services.decision_core.unified_gate import RR_EPS


def _gate_rr_ok(tp: float, sl: float, min_rr: float) -> bool:
    """unified_gate.evaluate_entry 内 RR 判定的等价谓词（与源码保持同一表达式）。"""
    return not ((tp / sl) < min_rr - RR_EPS)


def test_production_boundary_values_pass_gate():
    """实测导致 148 单被拦的精确值，修复后必须放行。"""
    tp, sl = 0.021291, 0.010645500000000117
    assert tp / sl < 2.0, "前提：该组值的 rr 确实在 2.0 之下（浮点噪声）"
    assert _gate_rr_ok(tp, sl, 2.0), "rr=1.9999999999999782 被 2.0 门槛拦下即为回归"


def test_clearly_low_rr_still_blocked():
    """容差不能放过真正不达标的单：RR 1.95 / 1.99 都必须拦。"""
    assert not _gate_rr_ok(0.0195, 0.01, 2.0)
    assert not _gate_rr_ok(0.0199, 0.01, 2.0)
    # 恰在容差外沿：2.0 - 2*EPS 拦，2.0 - EPS/2 放
    assert not _gate_rr_ok((2.0 - 2 * RR_EPS) * 0.01, 0.01, 2.0)
    assert _gate_rr_ok((2.0 - RR_EPS / 2) * 0.01, 0.01, 2.0)


def test_eps_is_economically_negligible():
    """RR_EPS 是浮点容差，不是门槛折扣。超过 1e-3 就已经是在改经济规则。"""
    assert 0 < RR_EPS <= 1e-3


@pytest.mark.parametrize("min_rr", [2.0, 2.5, 1.0])
def test_ensure_min_rr_output_always_passes_gate(min_rr, monkeypatch):
    """层间一致性：_ensure_min_rr 认为达标（或抬完 TP）的一律能过 gate。

    模拟生产噪声来源：sl_pct = |sl_price/entry - 1|（从价格反推），
    tp 由 market_aware_tpsl 六位小数舍入。
    """
    from backend.services.scalp.scalp_execution_gate import ScalpExecutionGate

    gate = ScalpExecutionGate()
    monkeypatch.setattr(
        gate, "_cfg",
        lambda key, default=None: min_rr if "MIN_RR" in str(key) else default,
    )
    rng = random.Random(20260902)
    for _ in range(3000):
        entry = rng.uniform(0.05, 80000.0)
        sl_nominal = round(rng.uniform(0.004, 0.03), 6)
        # 从价格反推引入浮点噪声（与 _adjust_sl_for_stop_hunt 的回算同源）
        sl_price = entry * (1.0 - sl_nominal)
        sl_pct = abs(sl_price / entry - 1.0)
        tp_pct = round(sl_nominal * min_rr, 6)
        tp_price = entry * (1.0 + tp_pct)
        new_tp, _ = gate._ensure_min_rr(
            entry, "long", sl_pct, tp_pct, tp_price, is_mr=(min_rr == 1.0), is_paper=True,
        )
        assert _gate_rr_ok(new_tp, sl_pct, min_rr), (
            f"层间不一致: entry={entry} sl={sl_pct!r} tp={new_tp!r} "
            f"rr={new_tp / sl_pct!r} min_rr={min_rr}"
        )
