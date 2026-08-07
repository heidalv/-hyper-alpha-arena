"""Analyst system extract: shim delegation smoke test."""
from __future__ import annotations


def test_analyst_system_shim_delegates(monkeypatch):
    from backend.services.full_auto import analyst_system_cycle as asc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}

    def _fake_run(db, session, active_ids, market_summary, host):
        called["run"] = True
        called["host"] = host
        host.pre_screen_passed = {"BTC"}
        host.mlto_handled_keys = {"k1"}

    def _fake_unified(db, session, account, active_ids, market_summary, host):
        called["unified"] = True
        called["unified_host"] = host

    monkeypatch.setattr(asc, "run_analyst_system", _fake_run)
    monkeypatch.setattr(asc, "run_analyst_system_unified", _fake_unified)
    monkeypatch.setattr(asc, "build_analyst_system_host", lambda svc: asc.AnalystSystemHost(
        market_scan_cache={},
        long_tier_staged_tp_state={},
        tick_symbol_subset={},
    ))

    svc = FullAutoTradingService.get_instance()
    svc._run_analyst_system(None, object(), ["s1"], {})
    assert called.get("run") is True
    assert isinstance(called.get("host"), asc.AnalystSystemHost)
    assert svc._pre_screen_passed == {"BTC"}
    assert svc._mlto_handled_keys == {"k1"}

    svc._run_analyst_system_unified(None, object(), object(), [], {})
    assert called.get("unified") is True
    assert isinstance(called.get("unified_host"), asc.AnalystSystemHost)
