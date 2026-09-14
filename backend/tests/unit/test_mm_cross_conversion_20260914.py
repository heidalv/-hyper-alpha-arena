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
              "nofill_min_notional", "nofill_stale", "nofill_other",
              # [F107] 判定区间空/非空
              "win_judged", "win_empty",
              # [F134] 地板价减仓腿漏斗（引擎账内配对口径）
              "floor_cross_fill", "floor_cross_nofill",
              "floor_nocross", "floor_win_empty"):
        assert k in r.cross_counts, f"缺少计数键 {k}"
        assert r.cross_counts[k] == 0
    st = r.status()
    assert "cross_counts" in st


def test_floor_funnel_is_wired_into_tick():
    """[F134] 地板漏斗必须在 tick 里记账，且判据与 F105 的穿越条件一致。

    背景：外部复算（挂单采样 + 墙钟配对）给出"地板时段 57% 转化"，但引擎自报
    ~100% ✓ ⇒ 外部复算被**配对差（±15~30s）**污染，无法判定是否漏判 ✗。
    唯一可信的口径是**引擎自己的配对**⇒ 把漏斗内建进 tick。
    """
    import inspect
    _tick = mmrunner.ShadowRunner.tick
    src = inspect.getsource(_tick)
    assert "FLOOR_QUOTE_BP" in src, "必须使用统一的地板阈值常量"
    assert 'self.cross_counts["floor_cross_fill"] += 1' in src
    assert 'self.cross_counts["floor_cross_nofill"] += 1' in src
    assert 'self.cross_counts["floor_nocross"] += 1' in src
    assert 'self.cross_counts["floor_win_empty"] += 1' in src
    # 判据必须复用与 plan_tick 对齐的 _hit_buy/_hit_sell（不能再造一套）
    assert "_hit_buy or _hit_sell" in src
    assert mmrunner.FLOOR_QUOTE_BP > 0


def test_floor_threshold_sits_above_reduce_floor():
    """阈值必须略高于 `min_width_reduce_bp`，否则偏斜/浮点会把地板腿漏掉。"""
    from backend.services.market_maker.core import QuoteParams
    qp = QuoteParams()
    assert mmrunner.FLOOR_QUOTE_BP >= float(qp.min_width_reduce_bp)
    assert mmrunner.FLOOR_QUOTE_BP <= 3.0, "不能把普通窄单也算成地板腿"


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
