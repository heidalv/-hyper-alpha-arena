"""回归：MltoCycleHost 必须挂上开仓执行所需方法（曾导致 open_ready 后 AttributeError）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock


def test_build_mlto_cycle_host_wires_evaluate_and_execute_proposal():
    from backend.services.full_auto.mlto_cycle import build_mlto_cycle_host

    eval_fn = MagicMock(return_value=True)
    acct_fn = MagicMock(return_value=14)
    svc = SimpleNamespace(
        _midlong_persistence_state={},
        _current_ai_tiers=["mid", "long"],
        _last_orch_decisions={},
        _last_orch_decisions_ts=0.0,
        _long_tier_staged_tp_state={},
        _mlto_handled_keys=set(),
        _mlto_handled_lock=MagicMock(),
        _inject_midlong_indicators=MagicMock(),
        _append_event=MagicMock(),
        _format_agent_event_detail=MagicMock(return_value=""),
        _try_execute_independent_agent_open=MagicMock(return_value=False),
        _persist_independent_scan_log=MagicMock(),
        _build_midlong_agent_envelope=MagicMock(return_value={}),
        _get_trading_account_id=acct_fn,
        _evaluate_and_execute_proposal=eval_fn,
    )

    host = build_mlto_cycle_host(svc)
    assert callable(host.evaluate_and_execute_proposal)
    assert callable(host.get_trading_account_id)
    assert host.evaluate_and_execute_proposal is eval_fn
    assert host.get_trading_account_id is acct_fn
