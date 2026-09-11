# -*- coding: utf-8 -*-
"""[2026-09-09 根因修复] LLM 方向不得单方面决定方向 + hub 归因落库 契约测试。

数据依据（`backend/scripts/audit_llm_direction_edge.py`，brain_theses × 1h K 线 n=192）：
  ALL/long  24h 胜率 0.434；ALL/short 24h 胜率 0.343；mid/short 24h 胜率 0.308
生产实况（alpha_analytics.ai_decision_logs）：mid/long 决策 hub_mode 恒
`ai_governed`（weight 0.6）→ direction 完全由 llm_qual 决定，校准表 0 行。

契约：
  1. LLM 想定方向但框架系信号反向 → 方向回落 orch_bias/framework（LLM 不改方向）；
  2. LLM 与框架同向 → LLM 可以定方向；
  3. 闸可一键回滚（MLTO_LLM_DIRECTION_REQUIRE_FW_AGREE=false 恢复旧行为）；
  4. hub 决策快照必须含 direction / dir_src / llm_qual / fw_mean（可归因）。
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

MOD = "backend.services.mlto.decision_hub"


def _fresh(monkeypatch, require_agree="true"):
    monkeypatch.setenv("MLTO_LLM_DIRECTION_REQUIRE_FW_AGREE", require_agree)
    monkeypatch.setenv("MLTO_AI_GOVERNED", "1")
    m = importlib.import_module(MOD)
    return importlib.reload(m)


def _sig(name, value, conf=0.85, source="llm"):
    from backend.services.mlto.types import Signal
    return Signal(name, value, conf, source)


def test_llm_short_vs_bullish_framework_falls_back(monkeypatch):
    m = _fresh(monkeypatch)
    signals = [_sig("llm_qual", 0.30), _sig("orch_mid_bias", 1.0, 0.5, "framework")]
    direction, src = m._derive_direction(signals, 0.5, ai_governed=True)
    assert direction == "long" and src == "orch_bias", (direction, src)


def test_llm_long_vs_bearish_framework_falls_back(monkeypatch):
    m = _fresh(monkeypatch)
    signals = [_sig("llm_qual", 0.80), _sig("orch_mid_bias", 0.0, 0.5, "framework")]
    direction, src = m._derive_direction(signals, 0.5, ai_governed=True)
    assert direction == "short" and src == "orch_bias", (direction, src)


def test_llm_short_with_bearish_framework_allowed(monkeypatch):
    m = _fresh(monkeypatch)
    signals = [_sig("llm_qual", 0.30), _sig("orch_mid_bias", 0.0, 0.5, "framework")]
    direction, src = m._derive_direction(signals, 0.5, ai_governed=True)
    assert direction == "short" and src == "llm_qual", (direction, src)


def test_llm_long_with_bullish_framework_allowed(monkeypatch):
    m = _fresh(monkeypatch)
    signals = [_sig("llm_qual", 0.80), _sig("orch_mid_bias", 1.0, 0.5, "framework")]
    direction, src = m._derive_direction(signals, 0.5, ai_governed=True)
    assert direction == "long" and src == "llm_qual", (direction, src)


def test_rollback_restores_llm_unilateral_direction(monkeypatch):
    m = _fresh(monkeypatch, require_agree="false")
    signals = [_sig("llm_qual", 0.30), _sig("orch_mid_bias", 1.0, 0.5, "framework")]
    direction, src = m._derive_direction(signals, 0.5, ai_governed=True)
    assert direction == "short" and src == "llm_qual", (direction, src)


def test_standard_mode_also_gated(monkeypatch):
    """非 ai_governed 模式下 llm_qual≤0.4 也不能单方面定 short。"""
    m = _fresh(monkeypatch)
    signals = [_sig("llm_qual", 0.30), _sig("orch_mid_bias", 1.0, 0.5, "framework")]
    direction, src = m._derive_direction(signals, 0.5, ai_governed=False)
    assert direction == "long" and src == "orch_bias", (direction, src)


def test_hub_decision_log_snapshot_fields():
    """落库快照必须含方向归因四要素（不连库，只测构造逻辑）。"""
    from backend.services.mlto.hub_decision_log import _fw_mean, _llm_qual_value

    signals = [_sig("llm_qual", 0.30), _sig("orch_mid_bias", 1.0, 0.5, "framework")]
    assert _llm_qual_value(signals) == 0.30
    assert _fw_mean(signals) is not None
