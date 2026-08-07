"""Master execution extract: shim delegation smoke test."""
from __future__ import annotations


def test_master_execution_shim_delegates(monkeypatch):
    from backend.services.full_auto import master_execution as me
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}

    def _fake_execute(*args, **kwargs):
        called["ok"] = True
        called["host"] = args[8] if len(args) > 8 else kwargs.get("host")
        return None

    monkeypatch.setattr(me, "execute_master_decisions", _fake_execute)
    monkeypatch.setattr(me, "build_master_execution_host", lambda svc: me.MasterExecutionHost(
        market_scan_cache={},
        partial_close_tracker={},
        deferred_signals={},
        last_reduce_time={},
        position_last_decision_ts={},
        master_strat_cache={},
        nature_to_tier_map={},
        position_min_decision_interval={"mid": 600},
        deferred_max_retries=3,
        sub_mgr=None,
    ))

    svc = FullAutoTradingService.get_instance()
    svc._execute_master_decisions(
        None, None, 1, [], [], [], {}, "paper",
    )
    assert called.get("ok") is True
    assert isinstance(called.get("host"), me.MasterExecutionHost)
