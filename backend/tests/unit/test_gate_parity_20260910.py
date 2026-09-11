# -*- coding: utf-8 -*-
"""[2026-09-10 第二十五轮] 分析口径 ↔ 生产门口径 **一致性契约测试**。

背景（§35）：分析脚本 `deep_long_freshness.learned_ok_v17` 的 chop 分支曾停留在
第十七轮的「无条件放行」，而生产在第十八轮已回滚为 `pos24≥60% 且 chg24≥+2%`。
本测试用**合成 K 线**驱动生产函数 `_long_learned_ok`，逐分支比对 `learned_ok_prod`，
防止两套口径再次漂移。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from backend.scripts.deep_long_freshness import (  # noqa: E402
    learned_ok_prod,
    learned_ok_v17,
)


def _bars(chg24: float, pos: float, base: float = 100.0):
    """构造 30 根 1h K 线，使 closes[-1]/closes[-25]-1 = chg24%、且末 24 根的 pos = pos%。

    生产口径：
      chg24 = closes[-1] / closes[-25] - 1
      pos   = (closes[-1] - lo) / (hi - lo) * 100，窗口 = 末 24 根
    """
    closes = [base] * 30
    closes[5] = base
    last = base * (1 + chg24 / 100.0)
    closes[29] = last
    # pos = (close - lo) / (hi - lo) → 令 hi = close + a、lo = close - b，需 b/(a+b) = pos%
    scale = base * 0.1
    a = max(100.0 - pos, 0.0) / 100.0 * scale
    b = max(pos, 0.0) / 100.0 * scale
    hi, lo = last + a, last - b
    for i in range(6, 29):
        closes[i] = (hi + lo) / 2.0
    bars = []
    for i in range(30):
        c = closes[i]
        bars.append({"close": c, "high": max(c, hi) if i >= 6 else c * 1.0001,
                     "low": min(c, lo) if i >= 6 else c * 0.9999})
    # 末根的高/低必须包住区间端点
    bars[29]["high"] = hi
    bars[29]["low"] = lo
    return bars


CASES = [
    # (regime, chg24, pos, 期望生产放行)
    ("up", 4.0, 50.0, True),
    ("up", 3.0, 50.0, True),      # 下边界含
    ("up", 2.9, 50.0, False),
    ("up", 5.9, 50.0, True),
    ("up", 6.0, 50.0, False),     # 上边界不含（spike 追入）
    ("up", 8.0, 50.0, False),
    ("chop", 3.0, 70.0, True),
    ("chop", 2.0, 60.0, True),    # 双边界含
    ("chop", 2.0, 59.0, False),
    ("chop", 1.0, 70.0, False),
    ("chop", 0.0, 90.0, False),
]


@pytest.mark.parametrize("regime,chg,pos,expect", CASES)
def test_learned_ok_prod_matches_production(monkeypatch, regime, chg, pos, expect):
    from backend.services.full_auto import midlong_circuit_gate as g

    monkeypatch.setattr(
        g, "_LONG_FEAT_CACHE", {}, raising=False
    )
    import backend.services.kline_data_service as kds

    monkeypatch.setattr(
        kds.kline_service, "get_aggregated_klines",
        lambda *a, **k: _bars(chg, pos), raising=True,
    )
    allowed, why = g._long_learned_ok("TESTSYM", regime)
    assert allowed is expect, f"{regime} chg={chg} pos={pos} → {why}"
    assert bool(learned_ok_prod(regime, pos, chg)) is expect, (
        f"分析口径 learned_ok_prod 与生产不一致：{regime} chg={chg} pos={pos}"
    )


def test_down_regime_is_blocked_by_combined_verdict():
    """down 由生产 `can_open_block_reason` 的 down 硬拦处理；
    `learned_ok_prod` 把 down 一并判 False（分析口径 = 综合判定）。"""
    assert learned_ok_prod("down", 80.0, 5.0) is False
    assert learned_ok_prod("", 80.0, 5.0) is False


def test_stale_v17_differs_on_chop_branch():
    """回归护栏：已废弃的 round-17 口径在 chop 分支上**必须**与生产不同，
    否则说明有人又把 chop 放宽了（§28.6 已回滚，§35 记录该口径 bug）。"""
    assert learned_ok_v17("chop", 10.0, -5.0) is True      # round-17：无条件放行
    assert learned_ok_prod("chop", 10.0, -5.0) is False    # 生产：pos<60 → 拦
    # up 分支两者一致（spike 上界都是 [3,6)）
    for chg in (2.0, 3.0, 4.5, 6.0, 9.0):
        assert learned_ok_v17("up", 50.0, chg) == learned_ok_prod("up", 50.0, chg)
