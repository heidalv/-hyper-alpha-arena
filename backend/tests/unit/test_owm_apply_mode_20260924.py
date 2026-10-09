# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R25] OWM 写入模式开关单测（conviction / shadow）。

背景：`dto.llm_conviction` 同时是下游开仓门槛的 confidence 输入
（`brain.py` L1806-1808 轮136 修复已明确禁止辩论折减写回该字段），
但 OWM 路径直接写它，且活跃会话长线权重钉在 clamp 下界 0.5
（实测 brain_subprocess.log：`OWM llm×0.500` 128 次，全部 tier=long）。
`shadow` 模式让"这半个信心值值多少笔开仓"可在模拟仓量化。
"""
from __future__ import annotations

import pytest

from backend.services.mlto import brain as B


@pytest.mark.parametrize(
    "val,expected",
    [
        (None, "conviction"),        # 未设置 ⇒ 保持现状
        ("", "conviction"),
        ("conviction", "conviction"),
        ("CONVICTION", "conviction"),
        ("shadow", "shadow"),
        (" Shadow ", "shadow"),
        ("garbage", "conviction"),   # 非法值安全回落，绝不静默切模式
    ],
)
def test_apply_mode(monkeypatch, val, expected):
    if val is None:
        monkeypatch.delenv("MLTO_OWM_INTO_BRAIN_MODE", raising=False)
    else:
        monkeypatch.setenv("MLTO_OWM_INTO_BRAIN_MODE", val)
    assert B._owm_apply_mode() == expected


def test_default_is_conviction_so_behaviour_unchanged(monkeypatch):
    """默认必须等于改动前行为（零惊喜切换）。"""
    monkeypatch.delenv("MLTO_OWM_INTO_BRAIN_MODE", raising=False)
    assert B._owm_apply_mode() == "conviction"
    # 并复核换算本身：×0.5 就是砍半
    assert B._apply_owm_to_conviction(42, 0.5) == 21
    assert B._apply_owm_to_conviction(55, 0.945) == 52
