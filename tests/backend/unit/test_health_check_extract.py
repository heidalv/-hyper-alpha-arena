"""Health check extract: shim delegation smoke test."""
from __future__ import annotations


def test_health_check_shim_delegates(monkeypatch):
    from backend.services.full_auto import health_check_cycle as hc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}

    def _fake_run(session_id, host, *, maintenance_only=False):
        called["ok"] = True
        called["session_id"] = session_id
        called["host"] = host
        called["maintenance_only"] = maintenance_only
        host.current_trace_id = "trace123"
        host.last_orch_decisions = {"BTC": "hold"}
        host.last_orch_decisions_ts = 99.0
        host.last_unified_snapshot = {"snap": 1}

    monkeypatch.setattr(hc, "run_health_check", _fake_run)
    monkeypatch.setattr(hc, "build_health_check_host", lambda svc: hc.HealthCheckHost(
        active_db_sessions={},
        market_scan_cache={},
        market_scan_cache_ts=0.0,
        last_orch_bias_by_symbol={},
        last_orch_decisions=None,
        last_orch_decisions_ts=0.0,
        last_unified_snapshot=None,
        defensive_entered_at={},
        recovery_until={},
        strategy_creation_ts={},
        unified_tick_count={},
        sub_mgr=None,
        nature_to_tier_map={},
        peak_decay_grace_hours=2.0,
        recovery_duration_hours=2.0,
        recovery_position_scale=0.5,
        strategy_creation_cooldown=600.0,
    ))

    svc = FullAutoTradingService.get_instance()
    svc._run_health_check("sess-1", maintenance_only=True)

    assert called.get("ok") is True
    assert called.get("session_id") == "sess-1"
    assert called.get("maintenance_only") is True
    assert isinstance(called.get("host"), hc.HealthCheckHost)
    assert svc._current_trace_id == "trace123"
    assert FullAutoTradingService._current_trace_id == "trace123"
    assert svc._last_orch_decisions == {"BTC": "hold"}
    assert svc._last_orch_decisions_ts == 99.0
    assert svc._last_unified_snapshot == {"snap": 1}
