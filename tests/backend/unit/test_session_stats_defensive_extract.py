"""Session stats + defensive extract smoke tests."""
from __future__ import annotations


def test_session_stats_shim_delegates(monkeypatch):
    from backend.services.full_auto import session_stats as ss
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    def _fake(db, session, active_ids, host):
        called["ok"] = True
        called["host"] = host

    monkeypatch.setattr(ss, "update_session_stats", _fake)
    monkeypatch.setattr(ss, "build_session_stats_host", lambda svc: ss.SessionStatsHost())

    FullAutoTradingService.get_instance()._update_session_stats(None, object(), [])
    assert called.get("ok") is True


def test_defensive_shims_delegate(monkeypatch):
    from backend.services.full_auto import defensive_cycle as dc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    host = dc.DefensiveHost(tier_protection={}, default_protection={})
    monkeypatch.setattr(dc, "build_defensive_host", lambda svc: host)
    monkeypatch.setattr(dc, "run_defensive_analysis", lambda *a, **k: called.setdefault("analysis", True))
    monkeypatch.setattr(dc, "run_defensive_verdicts", lambda *a, **k: called.setdefault("verdicts", True))
    monkeypatch.setattr(dc, "run_rule_based_defensive", lambda *a, **k: called.setdefault("rule", True))

    svc = FullAutoTradingService.get_instance()
    svc._execute_defensive_analysis(None, object(), {})
    svc._execute_defensive_verdicts(None, object(), 1, [], [])
    svc._rule_based_defensive(None, object(), [], {})
    assert called == {"analysis": True, "verdicts": True, "rule": True}
