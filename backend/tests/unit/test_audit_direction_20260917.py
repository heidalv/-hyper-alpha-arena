# -*- coding: utf-8 -*-
"""[调研轮32/33 2026-09-17] 审计方向补全的契约测试。

## 缺陷（轮31 实测 96h）

`midlong_long_regime_block` 276 行、`midlong_short_regime_block` 537 行的审计
`direction` **全为空**（写入点取 `hub_dir`，这两个闸不设它），而它们本质是方向性闸
（只拦多头 / 只拦空头）。后果：`audit_block_counterfactual.py` 因"方向不可知"拒绝猜测
⇒ "这道闸拦得对不对"无法复盘（long 车道为何 0/8 空仓也答不了）。

## 锁定语义

1. 只做**文本显式方向**映射；推断不出返回空串（**绝不猜**）；
2. 写入点必须真的用上它（接线护栏），且**不得破坏** `hub_dir` 的优先级。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.services.full_auto import midlong_executor as me  # noqa: E402


@pytest.mark.parametrize("reason,expect", [
    ("midlong_long_regime_block: 日线 regime=down（下行趋势），mid/long 多头在", "long"),
    ("midlong_short_regime_block: 日线 regime=ranging（非下行）", "short"),
    ("location_gate_veto: 24h区间分位78%≥60% 高位追多", "long"),
    ("location_gate_veto: 24h区间分位11%≤40% 低位追空", "short"),
    ("eval_false:v5gate [EVGate] EV=-1.4%", ""),          # 非方向性 ⇒ 不猜
    ("", ""),
    (None, ""),
])
def test_dir_from_reason(reason, expect):
    assert me._dir_from_reason(reason) == expect


def test_wired_into_audit_call():
    """接线护栏：审计调用必须用推理结果，且保留 hub_dir 优先。"""
    src = inspect.getsource(me)
    assert "direction=(hub_dir or _dir_from_reason(_reason))" in src, (
        "审计方向未接线（定义了却没用 = 等于没修）"
    )
    i = src.index("direction=(hub_dir or _dir_from_reason(_reason))")
    window = src[max(0, i - 800):i + 200]
    assert "stage=\"writer\"" in window, "必须作用于 writer 阶段审计（regime 闸就在这里）"


def test_unknown_reason_stays_unknown():
    """回归护栏：不得为了"能算"而给未知原因编造方向。"""
    for r in ("chart_gate_veto", "regime_extreme", "eval_false:decision_price_stale"):
        assert me._dir_from_reason(r) == ""
