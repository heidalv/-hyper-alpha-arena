"""Extract shim smoke tests for validate, v3, strategy maintenance."""
from __future__ import annotations


def test_validate_ai_shim_returns(monkeypatch):
    from backend.services.full_auto import ai_decision_audit as ada
    from backend.services.full_auto_trading_service import FullAutoTradingService

    monkeypatch.setattr(ada, "validate_ai_decisions", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ada, "build_ai_decision_audit_host", lambda svc: ada.AiDecisionAuditHost(
        nature_to_tier_map={}, health_status={},
    ))
    out = FullAutoTradingService.get_instance()._validate_ai_decisions(object(), {}, [], [])
    assert out == {"ok": True}


def test_analyst_v3_shim(monkeypatch):
    from backend.services.full_auto import analyst_system_v3_cycle as v3
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(v3, "run_analyst_system_v3", lambda *a, **k: called.setdefault("ok", True))
    monkeypatch.setattr(v3, "build_analyst_v3_host", lambda svc: v3.AnalystV3Host(active_db_sessions={}))
    FullAutoTradingService.get_instance()._run_analyst_system_v3("s", "running", 1, 2, [], {})
    assert called.get("ok") is True


def test_strategy_maintenance_shims(monkeypatch):
    from backend.services.full_auto import strategy_maintenance as sm
    from backend.services.full_auto_trading_service import FullAutoTradingService

    monkeypatch.setattr(sm, "cleanup_stale_strategies", lambda db, host: {"cleanup": True})
    monkeypatch.setattr(sm, "merge_duplicate_strategies", lambda db, sid, host: {"merge": sid})
    monkeypatch.setattr(sm, "build_strategy_maintenance_host", lambda svc: sm.StrategyMaintenanceHost())
    svc = FullAutoTradingService.get_instance()
    assert svc.cleanup_stale_strategies(None) == {"cleanup": True}
    assert svc.merge_duplicate_strategies(None, "sess-1") == {"merge": "sess-1"}
