"""MLTO 单元测试。"""
from __future__ import annotations

import uuid

import pytest


def test_decision_hub_fuse():
    from backend.services.mlto.decision_hub import fuse_signals
    from backend.services.mlto.types import Signal

    sigs = [Signal("quant_trend", 0.7, 0.8, "quant")]
    hub = fuse_signals(sigs, "mid")
    assert 0 <= hub.adjusted <= 1
    assert hub.open_readiness >= 0


def test_open_gate_describe():
    from backend.services.mlto import open_gate
    from backend.services.mlto.types import HubDecision, PerceptionPacket, ThesisDTO
    from datetime import datetime, timezone

    th = ThesisDTO(
        thesis_id=str(uuid.uuid4()),
        session_id="s",
        symbol="BTC",
        tier="mid",
        direction="long",
        open_readiness=50,
        review_count=1,
        stable_since=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    hub = HubDecision(
        action="WAIT", direction="long", composite=0.5, adjusted=0.5,
        consistency=0.7, open_readiness=50, reason_text="",
    )
    pkt = PerceptionPacket(
        symbol="BTC", tier="mid", session_id="s", ts=0, price=0,
        market_summary_sym={}, orchestrator={}, quant_brief={},
        analyst_reports={}, pre_screener_passed=True,
    )
    status = open_gate.describe_gate_status(th, hub, pkt, {})
    assert "summary" in status
    assert status["can_open"] is False


def test_envelope_mlto_fields():
    from backend.services.agent_decision_envelope import AgentDecisionEnvelope

    env = AgentDecisionEnvelope.new(
        "swing_agent",
        thesis_id="tid",
        evidence_chain_snapshot=[{"event_id": "e1"}],
        open_readiness_at_entry=72,
    )
    d = env.to_dict()
    assert d["thesis_id"] == "tid"
    assert d["open_readiness_at_entry"] == 72


def test_thesis_store_cache_restore():
    from backend.database.connection import AnalyticsBase, AnalyticsSessionLocal, analytics_engine
    from backend.services.mlto import thesis_store
    from backend.services.mlto.db_models import MltoThesis

    AnalyticsBase.metadata.create_all(bind=analytics_engine)
    db = AnalyticsSessionLocal()
    sid = f"ut-{uuid.uuid4().hex[:6]}"
    try:
        t = thesis_store.get_or_create(sid, "ETH", "mid", db=db)
        t.thesis_summary = "unit test"
        t.review_count = 4
        thesis_store._persist(db, t)
        tid = t.thesis_id
        thesis_store.clear_cache()
        t2 = thesis_store.get_or_create(sid, "ETH", "mid", db=db)
        assert t2.thesis_id == tid
        assert t2.review_count >= 4
    finally:
        db.close()
