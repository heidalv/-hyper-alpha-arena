# -*- coding: utf-8 -*-
"""p1-deep-analysis：tasks 入账 / 事件去重 / 可信度矩阵纯逻辑单测（不出网）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.services.analysis.model_gateway import ConsensusResult, ModelResult


@pytest.fixture
def tmp_data(monkeypatch, tmp_path):
    from backend.services.analysis import tasks as T

    monkeypatch.setattr(T, "DATA_DIR", tmp_path)
    return tmp_path


def _cres(task="daily_brief", *, accepted=True, score=0.85, final=None, primaries=None):
    final = final or {
        "direction": "bullish", "strength": 6, "confidence": 0.8,
        "regime": "trend_up", "regime_confidence": 0.7,
        "bucket_weights": {"trend": 0.6, "cashflow": 0.3, "research": 0.1},
        "symbol_views": [
            {"symbol": "BTCUSDT", "direction": "bullish", "strength": 7, "horizon_hours": 24, "reason": "r"},
            {"symbol": "ETH", "direction": "bullish", "strength": 5, "horizon_hours": 24, "reason": "r2"},
        ],
        "key_factors": ["a", "b"], "risks": ["x"], "summary": "ok",
    }
    primaries = primaries or [
        ModelResult(transport="minimax", model="m", task=task, ok=True, json=dict(final),
                    text="{}", confidence=None if False else None),  # type: ignore[arg-type]
    ]
    # ModelResult may not have confidence field — set via json only
    return ConsensusResult(
        task=task, status="ok", consensus_group="g1", consensus_score=score,
        accepted=accepted, final=final if accepted else None, primaries=primaries,
        run_id="run-1", context_hash="abc", notes=[],
    )


def test_ingest_daily_brief_writes_signals_and_bucket_file(tmp_data, monkeypatch):
    from backend.services.analysis import tasks as T
    from backend.services.analysis.context_pack import ContextPack

    signals, preds = [], []

    def _sig(**kw):
        signals.append(kw)
        return f"sig-{len(signals)}"

    def _pred(**kw):
        preds.append(kw)
        return f"pred-{len(preds)}"

    monkeypatch.setattr(T.ledgers, "record_signal", _sig)
    monkeypatch.setattr(T.ledgers, "record_prediction", _pred)

    pack = ContextPack(task="daily_brief", data_cutoff_ms=1, layers={"market": {}})
    # need a primary with ok json for prediction recording
    final = {
        "direction": "bullish", "strength": 6, "confidence": 0.8, "regime": "trend_up",
        "bucket_weights": {"trend": 0.55, "cashflow": 0.35, "research": 0.1},
        "symbol_views": [
            {"symbol": "ETHUSDT", "direction": "bearish", "strength": 4, "horizon_hours": 12, "reason": "r"},
        ],
        "key_factors": ["k"], "risks": [], "summary": "s",
    }
    p = ModelResult(transport="minimax", model="m", task="daily_brief", ok=True, json=final, text="{}")
    cres = ConsensusResult(
        task="daily_brief", status="ok", consensus_group="g", consensus_score=0.9,
        accepted=True, final=final, primaries=[p], run_id="r1", context_hash="h",
    )
    out = T.ingest_consensus("daily_brief", cres, pack)
    assert len(out["signals"]) >= 2  # BTC + ETH
    assert out["predictions"]
    bw = json.loads((tmp_data / "latest_bucket_weights.json").read_text(encoding="utf-8"))
    assert abs(bw["bucket_weights"]["trend"] - 0.55) < 1e-9
    assert (tmp_data / "latest_daily_brief.json").exists()


def test_ingest_weekly_creates_experiments(tmp_data, monkeypatch):
    from backend.services.analysis import tasks as T
    from backend.services.analysis.context_pack import ContextPack

    exps = []
    monkeypatch.setattr(T.ledgers, "record_signal", lambda **kw: "s1")
    monkeypatch.setattr(T.ledgers, "record_prediction", lambda **kw: "p1")
    monkeypatch.setattr(T.ledgers, "create_experiment", lambda **kw: (exps.append(kw), f"e{len(exps)}")[1])

    final = {
        "direction": "neutral", "strength": 3, "confidence": 0.7,
        "lane_assessments": [{"lane": "long", "verdict": "keep", "evidence": ["ok"]}],
        "experiments": [{
            "title": "降短线配额", "hypothesis": "短线净期望为负",
            "change": {"SHORT_DAILY_QUOTA": 2},
            "expected_metrics": [{"metric": "net_bp", "op": ">", "threshold": 0}],
            "window_hours": 168, "rollback_condition": "净 bp < -50",
        }],
        "key_factors": ["a"], "summary": "s",
    }
    p = ModelResult(transport="glm_opencode", model="g", task="weekly_review", ok=True, json=final, text="{}")
    cres = ConsensusResult(
        task="weekly_review", status="ok", consensus_group="g", consensus_score=0.8,
        accepted=True, final=final, primaries=[p], run_id="r2", context_hash="h2",
    )
    out = T.ingest_consensus("weekly_review", cres, ContextPack(task="weekly_review", data_cutoff_ms=1, layers={}))
    assert out["experiments"] == ["e1"]
    assert exps[0]["title"] == "降短线配额"
    assert exps[0]["source"] == "dual:weekly_review"


def test_ingest_rejects_below_threshold_still_records_preds(tmp_data, monkeypatch):
    from backend.services.analysis import tasks as T
    from backend.services.analysis.context_pack import ContextPack

    signals, preds = [], []
    monkeypatch.setattr(T.ledgers, "record_signal", lambda **kw: signals.append(kw) or "s")
    monkeypatch.setattr(T.ledgers, "record_prediction", lambda **kw: preds.append(kw) or "p")

    final = {"direction": "bullish", "strength": 5, "confidence": 0.5, "key_factors": [], "summary": "s"}
    p = ModelResult(transport="deepseek", model="d", task="daily_brief", ok=True, json=final, text="{}")
    cres = ConsensusResult(
        task="daily_brief", status="degraded", consensus_group="g", consensus_score=0.4,
        accepted=False, final=final, primaries=[p], run_id="r3", context_hash="h3",
    )
    out = T.ingest_consensus("daily_brief", cres, ContextPack(task="daily_brief", data_cutoff_ms=1, layers={}))
    assert out["signals"] == []
    assert out["predictions"]
    latest = json.loads((tmp_data / "latest_daily_brief.json").read_text(encoding="utf-8"))
    assert latest["final"] is None


def test_event_evaluated_memo(tmp_data):
    from backend.services.analysis import tasks as T

    assert not T.is_event_evaluated(42)
    T._mark_event_evaluated(42, "run-x", 0.88)
    assert T.is_event_evaluated(42)
    prior = T._load_evaluated()["events"]["42"]
    assert prior["run_id"] == "run-x"


def test_scan_skips_evaluated(tmp_data, monkeypatch):
    from backend.services.analysis import tasks as T

    T._mark_event_evaluated(1, "old", 0.9)
    rows = [
        {"id": 1, "event_type": "funding.extreme", "severity": 4, "ts_ms": 100},
        {"id": 2, "event_type": "news.high_impact", "severity": 5, "ts_ms": 200},
        {"id": 3, "event_type": "whale.large", "severity": 2, "ts_ms": 300},  # severity filter in recent, still in list
    ]

    class FakeMes:
        @staticmethod
        def recent(**kw):
            return [r for r in rows if r["severity"] >= kw.get("min_severity", 0)]

    monkeypatch.setattr("backend.services.events.market_events_store.recent", FakeMes.recent)
    # patch import path used inside scan
    import backend.services.events.market_events_store as mes
    monkeypatch.setattr(mes, "recent", FakeMes.recent)

    called = []

    def _fake_impact(*, event=None, dry_run=False):
        called.append(event["id"])
        return {"event_id": event["id"], "accepted": True, "status": "ok"}

    monkeypatch.setattr(T, "run_event_impact", _fake_impact)
    monkeypatch.setattr(T, "tasks_enabled", lambda: True)

    out = T.scan_and_eval_events(limit=5)
    assert called == [2]  # 1 already evaluated; 3 not in _EVENT_EVAL wait whale.large IS in list
    # severity 2 is filtered by min_severity default 3 in recent() call — FakeMes filters by min_severity
    # so id=3 with sev=2 won't appear if min_severity=3
    assert out["accepted"] == 1


def test_norm_symbol():
    from backend.services.analysis.tasks import _norm_symbol

    assert _norm_symbol("btcusdt") == "BTC"
    assert _norm_symbol("ETH-PERP") == "ETH"
    assert _norm_symbol("SOL/USDT") == "SOL"
