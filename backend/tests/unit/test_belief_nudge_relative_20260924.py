# -*- coding: utf-8 -*-
"""[续作 R16] belief nudge 口径（relative vs legacy）+ 衰减回补 的单测。

背景（§42.3 实测）：`_apply_owm_nudge` 旧口径是**绝对**步长 llm −0.03 / framework +0.015、
clamp [0.5,1.5]、**无衰减**——16 次触发把活跃会话 `fa_7e12e7a1b6` 的 `long llm` 从 1.0
压到 clamp 下界 0.5 且永不回收（framework=1.24=1.0+16×0.015 为算术佐证）。
且下行步长（0.03）是 `_bump_owm` 相对步长（≈0.015）的 **2 倍**，口径不一致。
"""
from __future__ import annotations

import pytest

from backend.services.mlto import midlong_belief_loop as B


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MIDLONG_BELIEF_OWM_NUDGE_MODE", raising=False)
    yield


def test_mode_default_relative_and_rollback(monkeypatch):
    assert B._owm_nudge_mode() == "relative"
    monkeypatch.setenv("MIDLONG_BELIEF_OWM_NUDGE_MODE", "legacy")
    assert B._owm_nudge_mode() == "legacy"
    monkeypatch.setenv("MIDLONG_BELIEF_OWM_NUDGE_MODE", "garbage")
    assert B._owm_nudge_mode() == "legacy"  # 非法值 fail-closed 到旧行为


def test_legacy_deltas_unchanged():
    assert B._nudge_deltas("legacy", -0.03) == [("llm", -0.03), ("framework", 0.015)]


def test_relative_deltas_use_base_weight(monkeypatch):
    import backend.services.mlto.learning_bridge as LB
    monkeypatch.setattr(LB, "_base_weight_for_source", lambda src, tier: 0.30)
    d = B._nudge_deltas("relative", -0.03)
    assert d == [("llm", -0.015), ("framework", 0.0075)]  # 0.30×5%=0.015；framework 半额
    # 即使调用方传的绝对步长不同，relative 也以基础权重为准
    assert B._nudge_deltas("relative", -0.99) == [("llm", -0.015), ("framework", 0.0075)]


def test_decay_math():
    assert B._nudge_decay_weight(0.5, 0.0) == pytest.approx(0.5)        # 不足 1 小时不动
    assert B._nudge_decay_weight(0.5, 1.0) == pytest.approx(0.525)      # 1 小时回补 5% 距离
    r = B._nudge_decay_weight(0.5, 24.0)
    assert 0.85 < r < 0.9, r                                            # 24h ≈ 1 − 0.5×0.95^24 ≈ 0.854
    assert B._nudge_decay_weight(1.4, 500.0) <= 1.5                     # clamp 上界
    assert B._nudge_decay_weight(0.2, 0.0) == 0.5                       # clamp 下界


def test_decay_robust_to_bad_input():
    assert B._nudge_decay_weight(0.5, None) == pytest.approx(0.5)
    assert B._nudge_decay_weight(0.5, "garbage") == pytest.approx(0.5)
    # None 起步 = 1.0（已是回收目标）⇒ 衰减不动、保持 1.0
    assert B._nudge_decay_weight(None, 1.0) == pytest.approx(1.0)


def test_apply_site_uses_new_helpers():
    """源码守卫：应用点必须使用新口径 + 衰减。"""
    from pathlib import Path
    src = Path(r"D:\001Alpha\Hyper-Alpha-Arena\backend\services\mlto\midlong_belief_loop.py").read_text(encoding="utf-8")
    assert "_nudge_deltas(_owm_nudge_mode()" in src
    assert "_nudge_decay_weight" in src
    # 旧口径（硬编码 0.03 比例）不应再出现在应用点
    body = src[src.find("def _apply_owm_nudge"):]
    assert '("llm", llm_delta), ("framework", abs(llm_delta) * 0.5)' not in body
