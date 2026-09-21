# -*- coding: utf-8 -*-
"""[轮154 2026-09-21] paper 下单必须消费缩仓链乘子（方案 C）。

## 现场
UNI mid（reports/_轮154_UNI连续开多_根因_20260921.md）：六层缩仓把
`decision["size_multiplier"]` 压到 `0.0008`，`[SizeFloor] PROBE-CLAMP` 抬到 `0.0714`
（目标名义 ≈$60），但 `paper_execution.py` **全文 0 次读取该字段**（grep 实测）⇒
实际成交 = `equity × MIDLONG_TIER_MARGIN_PCT_MID(0.10) × 3` = $1387 名义，
被 `PC_MAX_WEIGHT_PER_SYMBOL_MID(0.15)` 压到 **$693 名义 / $231 保证金** —— 风险链意图的 11.5×。
实盘路径本来就消费它（`live_trading.py`「下游缩仓乘子必须计入敞口估算」），paper 是漏的。

## 口径
- 只有 `respect_raw_sizing=False`（tier 目标 sizing 路径）才补乘 —— 上游 SizingPlan 的
  notional 已含各自乘子，二次相乘会重复缩仓（live 侧实测 $5.5→$0.12）。
- 回滚：`PAPER_APPLY_SIZE_MULTIPLIER=false`。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.full_auto import paper_execution as PE  # noqa: E402


def _plan(**over):
    base = dict(action="open", margin_usd=462.0, notional_usd=1386.56, size_pct=0.33)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("PAPER_APPLY_SIZE_MULTIPLIER", raising=False)
    yield


def test_multiplier_reaches_the_order():
    """UNI 实例：×0.0714 ⇒ $1386.56 → $99 名义、$462 → $33 保证金。"""
    plan = _plan()
    applied = PE._scale_plan_by_size_chain(plan, 0.0714, respect_raw_sizing=False)
    assert applied == pytest.approx(0.0714)
    assert plan.notional_usd == pytest.approx(99.0, abs=0.5)
    assert plan.margin_usd == pytest.approx(33.0, abs=0.5)
    assert plan.size_pct == pytest.approx(0.33 * 0.0714, rel=1e-6)


def test_no_double_shrink_when_upstream_sizing_plan():
    """上游 SizingPlan 已含乘子 ⇒ 不再二次相乘（否则 $5.5→$0.12 那类事故）。"""
    plan = _plan(margin_usd=1.83, notional_usd=5.5)
    applied = PE._scale_plan_by_size_chain(plan, 0.0714, respect_raw_sizing=True)
    assert applied == 1.0
    assert plan.notional_usd == pytest.approx(5.5), "不得二次缩仓"


def test_no_scaling_for_close_or_equal_one():
    plan = _plan(action="close")
    assert PE._scale_plan_by_size_chain(plan, 0.5, respect_raw_sizing=False) == 1.0
    assert plan.notional_usd == pytest.approx(1386.56)
    plan2 = _plan()
    assert PE._scale_plan_by_size_chain(plan2, 1.0, respect_raw_sizing=False) == 1.0
    assert plan2.notional_usd == pytest.approx(1386.56)


def test_rollback_switch(monkeypatch):
    monkeypatch.setenv("PAPER_APPLY_SIZE_MULTIPLIER", "false")
    plan = _plan()
    assert PE._scale_plan_by_size_chain(plan, 0.0714, respect_raw_sizing=False) == 1.0
    assert plan.notional_usd == pytest.approx(1386.56), "回滚开关未生效"


def test_garbage_multiplier_is_ignored():
    plan = _plan()
    assert PE._scale_plan_by_size_chain(plan, None, respect_raw_sizing=False) == 1.0
    assert PE._scale_plan_by_size_chain(plan, "abc", respect_raw_sizing=False) == 1.0
    assert plan.notional_usd == pytest.approx(1386.56)


def test_execute_paper_trade_actually_calls_it():
    """接线护栏：只定义不调用 = 没修（用户历史上的高频抱怨）。"""
    src = (ROOT / "backend/services/full_auto/paper_execution.py").read_text(encoding="utf-8-sig")
    assert "_scale_plan_by_size_chain(" in src.split("def execute_paper_trade")[1], \
        "execute_paper_trade 里没有调用缩仓乘子 —— 又变成『定义了但不接线』"
