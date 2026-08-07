"""AI decisions extract: shim delegation smoke test."""
from __future__ import annotations


def test_ai_decisions_shim_delegates(monkeypatch):
    from backend.services.full_auto import ai_decisions as ad
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}

    def _fake_execute(db, session, active_ids, market_data, host):
        called["ok"] = True
        called["host"] = host
        called["active_ids"] = active_ids
        called["market_data"] = market_data

    monkeypatch.setattr(ad, "execute_ai_decisions", _fake_execute)
    monkeypatch.setattr(ad, "build_ai_decisions_host", lambda svc: ad.AiDecisionsHost(
        last_unified_snapshot={"snap": 1},
    ))

    svc = FullAutoTradingService.get_instance()
    svc._execute_ai_decisions(None, object(), ["s1"], {"BTC": {}})

    assert called.get("ok") is True
    assert isinstance(called.get("host"), ad.AiDecisionsHost)
    assert called.get("active_ids") == ["s1"]
    assert called.get("market_data") == {"BTC": {}}
