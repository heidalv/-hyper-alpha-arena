"""Proposal execution extract smoke test."""
from __future__ import annotations


def test_proposal_exec_shim(monkeypatch):
    from backend.services.full_auto import proposal_execution as pe
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(pe, "evaluate_and_execute_proposal", lambda **k: called.setdefault("ok", k.get("proposal")))
    monkeypatch.setattr(pe, "build_proposal_execution_host", lambda svc: pe.ProposalExecutionHost())

    sentinel = object()
    svc = FullAutoTradingService.get_instance()
    svc._evaluate_and_execute_proposal(
        db=None, session=None, proposal=sentinel, market_summary={},
    )
    assert called.get("ok") is sentinel
