"""MLTO / quick orch / hold-trend extract smoke tests."""
from __future__ import annotations


def test_mlto_maintain_shim(monkeypatch):
    from backend.services.full_auto import mlto_cycle as mc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(
        mc,
        "maintain_mlto_theses_for_session",
        lambda **k: called.setdefault("ok", k.get("mode")),
    )
    monkeypatch.setattr(mc, "build_mlto_cycle_host", lambda svc: mc.MltoCycleHost())

    FullAutoTradingService.get_instance()._maintain_mlto_theses_for_session(
        session=object(),
        market_summary={},
        analyst_reports={},
        mode="paper",
        portfolio={},
    )
    assert called.get("ok") == "paper"


def test_mlto_execute_shim(monkeypatch):
    from backend.services.full_auto import mlto_cycle as mc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    monkeypatch.setattr(
        mc,
        "execute_mlto_lane",
        lambda **k: ("hold", "x", 50),
    )
    monkeypatch.setattr(mc, "build_mlto_cycle_host", lambda svc: mc.MltoCycleHost())

    out = FullAutoTradingService.get_instance()._execute_mlto_lane(
        sym="BTC",
        dec={"action": "hold"},
        tier="mid",
        agent_source="swing",
        market_summary={},
        analyst_reports={},
        db=None,
        session=None,
        mode="paper",
        portfolio={},
    )
    assert out == ("hold", "x", 50)


def test_quick_orch_shim(monkeypatch):
    from backend.services.full_auto import quick_orchestrator_eval as qe
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(
        qe,
        "run_quick_orchestrator_eval",
        lambda sid, host: called.setdefault("sid", sid),
    )
    monkeypatch.setattr(
        qe,
        "build_quick_orch_host",
        lambda svc: qe.QuickOrchHost(active_db_sessions={}, deadlock_rescue_count={}),
    )
    FullAutoTradingService.get_instance()._run_quick_orchestrator_eval("sess-q")
    assert called.get("sid") == "sess-q"


def test_hold_trend_shims(monkeypatch):
    from backend.services.full_auto import hold_timeout_trend_review as ht
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    host = ht.HoldTrendReviewHost(
        active_db_sessions={},
        last_hold_timeout_ai_review={},
    )
    monkeypatch.setattr(ht, "build_hold_trend_review_host", lambda svc: host)
    monkeypatch.setattr(
        ht,
        "run_hold_timeout_ai_review_if_needed",
        lambda sid, h, **k: called.setdefault("if", sid),
    )
    monkeypatch.setattr(
        ht,
        "run_hold_timeout_ai_review",
        lambda db, session, pending, h: called.setdefault("rev", True),
    )
    monkeypatch.setattr(
        ht,
        "run_trend_review",
        lambda db, session, account_id, market_summary, h: called.setdefault("trend", account_id),
    )

    svc = FullAutoTradingService.get_instance()
    svc._run_hold_timeout_ai_review_if_needed("sess-h")
    svc._run_hold_timeout_ai_review(None, None, [])
    svc._run_trend_review(None, None, 7, {})
    assert called.get("if") == "sess-h"
    assert called.get("rev") is True
    assert called.get("trend") == 7
