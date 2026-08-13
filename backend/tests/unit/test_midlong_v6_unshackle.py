# -*- coding: utf-8 -*-
"""v6 中长线松绑验收：权威/安全网/方向审计。"""
from __future__ import annotations

import os

import pytest

from backend.config import settings
from backend.services.full_auto import midlong_executor as mex
from backend.services.mlto import open_gate
from backend.services.mlto.midlong_direction_audit import (
    record_decision_audit,
    record_open_audit,
    summarize_consistency,
    summarize_decision_funnel,
)
from backend.services.mlto.types import HubDecision, PerceptionPacket, ThesisDTO


def test_paper_default_authority_is_mlto(monkeypatch):
    monkeypatch.setattr(
        "backend.config.settings.MIDLONG_EXEC_AUTHORITY", "", raising=False,
    )
    monkeypatch.setattr(
        "backend.config.settings.MIDLONG_MLTO_CONTROLS_EXEC", False, raising=False,
    )
    monkeypatch.setattr(
        "backend.config.settings.PAPER_FAST_TRIAL", False, raising=False,
    )
    monkeypatch.setenv("TRADING_MODE", "paper")
    assert mex.get_midlong_exec_authority("paper") == "mlto"


def test_explicit_trend_authority_honored(monkeypatch):
    monkeypatch.setattr(
        "backend.config.settings.MIDLONG_EXEC_AUTHORITY", "trend", raising=False,
    )
    assert mex.get_midlong_exec_authority("paper") == "trend"


def test_open_gate_recommend_false_soft_on_nibble_probe(monkeypatch):
    monkeypatch.setattr(settings, "MIDLONG_THESIS_OPEN_GATE", True)
    monkeypatch.setattr(settings, "MIDLONG_NIBBLE_PROBE_ENABLED", True, raising=False)
    thesis = ThesisDTO(
        thesis_id="t", session_id="s", symbol="BTC", tier="long",
        direction="long", open_readiness=80, review_count=3,
        recommend_open=False, thesis_summary="long",
    )
    hub = HubDecision(
        action="NIBBLE", direction="long", composite=0.5, adjusted=0.5,
        consistency=1.0, open_readiness=50, reason_text="probe",
        mode="ai_governed", dir_src="nibble_probe_llm",
    )
    packet = PerceptionPacket(
        symbol="BTC", tier="long", session_id="s", ts=0.0, price=100000.0,
        market_summary_sym={"current_price": 100000.0},
        orchestrator={}, quant_brief={}, analyst_reports={},
        trading_mode="paper",
    )
    ok, why = open_gate.allow(thesis, hub, packet, {})
    assert ok is True
    assert "recommend_open_false_soft" in why
    monkeypatch.setattr(settings, "MIDLONG_THESIS_OPEN_GATE", True)
    monkeypatch.setattr(
        "backend.services.mlto.midlong_trade_design.is_chop_regime",
        lambda *a, **k: (True, "unit_chop"),
    )
    thesis = ThesisDTO(
        thesis_id="t", session_id="s", symbol="BTC", tier="long",
        direction="long", open_readiness=80, review_count=3,
        recommend_open=True, thesis_summary="long",
    )
    hub = HubDecision(
        action="BUILD", direction="long", composite=0.7, adjusted=0.65,
        consistency=1.0, open_readiness=65, reason_text="ok",
        mode="ai_governed", dir_src="llm_qual",
    )
    packet = PerceptionPacket(
        symbol="BTC", tier="long", session_id="s", ts=0.0, price=100000.0,
        market_summary_sym={"current_price": 100000.0},
        orchestrator={}, quant_brief={}, analyst_reports={},
    )
    ok, why = open_gate.allow(thesis, hub, packet, {})
    assert ok is True
    assert "chop_soft" in why


def test_direction_audit_consistency(tmp_path, monkeypatch):
    path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(path))
    record_open_audit(
        symbol="BTC", fill_dir="buy", thesis_dir="long", hub_dir="long",
        sl_source="llm", mode="ai_governed", dir_src="llm_qual", authority="mlto",
    )
    record_open_audit(
        symbol="ETH", fill_dir="sell", thesis_dir="long", hub_dir="long",
        sl_source="llm", mode="ai_governed", dir_src="llm_qual", authority="mlto",
    )
    s = summarize_consistency(48.0)
    assert s["comparable"] == 2
    assert s["consistent"] == 1
    assert s["flips"] == 1
    assert s["rate"] == 0.5


def test_decision_funnel_audit(tmp_path, monkeypatch):
    path = tmp_path / "audit_funnel.jsonl"
    monkeypatch.setenv("MIDLONG_DIRECTION_AUDIT_PATH", str(path))
    record_decision_audit(
        outcome="skip", stage="trend", symbol="BTC",
        reason="score_low(30<32)", score=30, session_id="fa_test",
    )
    record_decision_audit(
        outcome="skip", stage="gate", symbol="ETH",
        reason="gate:direction neutral", hub_action="WAIT",
    )
    record_decision_audit(
        outcome="open_attempt", stage="hub", symbol="SOL",
        reason="hub:NIBBLE", action="buy", hub_action="NIBBLE",
    )
    record_open_audit(
        symbol="SOL", fill_dir="buy", thesis_dir="long", hub_dir="long",
        authority="mlto", session_id="fa_test",
    )
    f = summarize_decision_funnel(48.0)
    assert f["skips"] == 2
    assert f["open_attempts"] == 1
    assert f["opened"] == 1
    assert f["by_stage_skip"].get("trend") == 1
    assert f["by_stage_skip"].get("gate") == 1
    assert any(r["reason"].startswith("score_low") for r in f["top_skip_reasons"])


def test_normalize_midlong_nature_preserves_swing():
    assert mex.normalize_midlong_nature("swing", "mid") == "swing"
    assert mex.normalize_midlong_nature("", "mid") == "swing"
    assert mex.normalize_midlong_nature("mid", "mid") == "swing"
    assert mex.normalize_midlong_nature("trend_follow", "long") == "trend_follow"
    assert mex.normalize_midlong_nature("position", "long") == "position"
    assert mex.normalize_midlong_nature("scalp", "short") == "scalp"


def test_factor_anchor_env_default_off():
    """本轮禁止因子进 Hub 投票。"""
    v = os.getenv("FEATURE_MIDLONG_FACTOR_ANCHOR_ENABLED", "false").lower()
    assert v not in ("1", "true", "yes", "on")
