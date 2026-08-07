"""Smoke tests for refresh / strategy creation / symbol risk extracts."""
from __future__ import annotations


def test_refresh_positions_shim(monkeypatch):
    from backend.services.full_auto import refresh_positions as rp
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(
        rp,
        "refresh_positions_local",
        lambda *a, **k: called.setdefault("ok", (1.0, 2.0)),
    )
    monkeypatch.setattr(rp, "build_refresh_positions_host", lambda svc: rp.RefreshPositionsHost())
    out = FullAutoTradingService.get_instance()._refresh_positions_local(
        None, 1, [], {}, {},
    )
    assert out == (1.0, 2.0)
    assert called.get("ok") == (1.0, 2.0)


def test_strategy_creation_shims(monkeypatch):
    from backend.services.full_auto import strategy_creation as sc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    monkeypatch.setattr(sc, "try_create_from_template", lambda *a, **k: "sid-tpl")
    assert svc._try_create_from_template(None, "BTC", "mid", 1, "moderate", "paper") == "sid-tpl"

    monkeypatch.setattr(sc, "auto_create_strategy", lambda *a, **k: "sid-auto")
    monkeypatch.setattr(sc, "build_strategy_creation_host", lambda svc: sc.StrategyCreationHost())
    assert svc._auto_create_strategy(None, None, "BTC", {}) == "sid-auto"

    assert "mid" in svc._infer_timeframe_slots({"market_cycle": "bull"})
    assert svc._infer_timeframe_slot({"market_cycle": "ranging"}) in ("short", "mid", "long")


def test_symbol_risk_shims(monkeypatch):
    from backend.services.full_auto import symbol_risk as sr
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    host = sr.SymbolRiskHost(state_lock=__import__("threading").Lock())
    monkeypatch.setattr(sr, "build_symbol_risk_host", lambda svc: host)

    called = {}
    monkeypatch.setattr(sr, "evaluate_dynamic_risk", lambda *a, **k: called.setdefault("dyn", True))
    svc._evaluate_dynamic_risk(object(), {})
    assert called.get("dyn") is True

    monkeypatch.setattr(sr, "check_per_symbol_risk", lambda *a, **k: sr.PerSymbolRiskResult(global_freeze=True))
    assert svc._check_per_symbol_risk(None, None).global_freeze is True

    monkeypatch.setattr(sr, "check_global_risk", lambda *a, **k: "dd")
    assert svc._check_global_risk(None, None) == "dd"

    assert svc._PerSymbolRiskResult is sr.PerSymbolRiskResult
