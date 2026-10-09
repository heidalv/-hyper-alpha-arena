# -*- coding: utf-8 -*-
"""[2026-09-24 新目标 R21] OWM 源兜底策略单测。

背景：`mlto_memory_events` 长期 0 行 ⇒ `_bump_owm` 的 cited_ids 恒为空，
每一笔平仓都被记到 'llm' 源（68 笔 mlto 平仓全部如此），属"来源归因失明"。
本单测锁定三种兜底模式的语义 + 默认值向后兼容（=历史行为，可零风险回滚）。
"""
from __future__ import annotations

import pytest

from backend.services.mlto import learning_bridge as LB


def test_default_is_llm_backward_compatible(monkeypatch):
    """未设开关时必须保持历史行为（默认 'llm'）。"""
    monkeypatch.delenv("MLTO_OWM_UNKNOWN_ATTRIB", raising=False)
    assert LB._owm_unknown_attrib() == "llm"
    assert LB._owm_fallback_sources({"entry_source": "trend_e1"}) == ["llm"]


def test_unknown_mode_does_not_credit_llm(monkeypatch):
    """unknown 模式：不给 llm 记虚假信用。"""
    monkeypatch.setenv("MLTO_OWM_UNKNOWN_ATTRIB", "unknown")
    assert LB._owm_fallback_sources({}) == ["unknown"]


def test_lane_mode_uses_entry_source_then_tier(monkeypatch):
    monkeypatch.setenv("MLTO_OWM_UNKNOWN_ATTRIB", "lane")
    assert LB._owm_fallback_sources({"entry_source": "Trend_E1"}) == ["trend_e1"]
    # entry_source 缺失时退到 timeframe_tier
    assert LB._owm_fallback_sources({"timeframe_tier": "MID"}) == ["mid"]
    # 两者都缺失时仍要有确定值，不能抛异常
    assert LB._owm_fallback_sources({}) == ["unknown"]


@pytest.mark.parametrize("bad", ["", "  ", "LLM2", "garbage", "none"])
def test_invalid_mode_falls_back_to_llm(monkeypatch, bad):
    """非法取值必须安全回落到默认，不能改变行为也不能崩。"""
    monkeypatch.setenv("MLTO_OWM_UNKNOWN_ATTRIB", bad)
    assert LB._owm_unknown_attrib() == "llm"
    assert LB._owm_fallback_sources({}) == ["llm"]


def test_lane_source_is_not_a_known_owm_source():
    """lane 模式的源名不在 OWM 信号映射表内 ⇒ 不映射到任何信号。

    这是 lane 模式的设计意图：宁可"记了但不生效"，也不要把证据
    错误地加到 llm/quant 的权重上。若未来把车道名加进映射表，
    本断言会失败以提醒复审。
    """
    for lane in ("trend_e1", "mid", "long", "unknown"):
        assert lane not in LB._OWM_SOURCE_TO_SIGNAL_LONG
        assert lane not in LB._OWM_SOURCE_TO_SIGNAL_MID
