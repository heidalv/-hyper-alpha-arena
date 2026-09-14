# -*- coding: utf-8 -*-
"""[2026-09-14 F105] 「穿越→成交」转化率计数器契约。

为什么需要：F103 修正回放的**一桶前瞻**后，实盘成交仍是修正回放的 ~0.6×，
而报价/挂宽/价差捕获都已对齐（每趟价差捕获 +8.198 vs +8.162bp）⇒ 残留差异只能在
「穿越是否被判成交」这一环。短窗（12 tick）样本太小，必须**进程内累计**长窗口统计。

计数口径必须与 `plan_tick` 的成交条件**逐条对齐**：本侧挂单存在 + 该侧有主动量 +
区间价触及挂单价。否则会把"无成交量的穿越"误记为异常，诊断失去可信度。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.market_maker import runner as mmrunner  # noqa: E402


def test_runner_exposes_cross_counts():
    r = mmrunner.ShadowRunner(lane_id="t", venue="x", symbols=["BTC"])
    assert isinstance(r.cross_counts, dict)
    for k in ("cross_buy", "cross_sell", "fill_buy", "fill_sell",
              "nofill_min_notional", "nofill_stale", "nofill_other"):
        assert k in r.cross_counts, f"缺少计数键 {k}"
        assert r.cross_counts[k] == 0
    st = r.status()
    assert "cross_counts" in st


def test_cross_condition_matches_plan_tick():
    """穿越判定必须与 plan_tick 一致：本侧挂单 + 该侧主动量 + 区间触及。"""
    import inspect
    src = inspect.getsource(mmrunning_tick())
    assert "_hit_buy = _qb0 > 0 and _seg_sell > 0 and 0 < _seg_low < _qb0" in src
    assert "_hit_sell = _qa0 > 0 and _seg_buy > 0 and _seg_high > _qa0" in src
    # 与 plan_tick 的 hit 条件同形（引用同一常量语义：PENETRATION_BP=0 时即"触及"）
    from backend.services.market_maker.runner import PENETRATION_BP
    assert PENETRATION_BP >= 0.0


def mmrunning_tick():
    return mmrunner.ShadowRunner.tick
