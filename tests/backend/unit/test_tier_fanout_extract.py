"""Tier fanout extract: shim delegation smoke test."""
from __future__ import annotations


def test_expand_multi_tier_shim_delegates(monkeypatch):
    from backend.services.full_auto import tier_fanout as tf
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}

    def _fake_expand(decisions, strat_tier_map, orch_directions, session, host):
        called["ok"] = True
        called["host"] = host
        called["decisions"] = decisions
        return [{"expanded": True}]

    monkeypatch.setattr(tf, "expand_multi_tier_decisions", _fake_expand)
    monkeypatch.setattr(tf, "build_tier_fanout_host", lambda svc: tf.TierFanoutHost(
        nature_to_tier_map={"swing": "mid"},
    ))

    svc = FullAutoTradingService.get_instance()
    result = svc._expand_multi_tier_decisions(
        [{"symbol": "BTC", "operation": "buy"}],
        {("BTC", "mid"): object()},
        {"BTC": {"mid_bias": "bullish"}},
        object(),
    )
    assert called.get("ok") is True
    assert isinstance(called.get("host"), tf.TierFanoutHost)
    assert result == [{"expanded": True}]
