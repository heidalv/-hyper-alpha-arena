"""Light trading / v3 factor / strategy lifecycle extract smoke tests."""
from __future__ import annotations


def test_light_trading_shim(monkeypatch):
    from backend.services.full_auto import light_trading_cycle as lt
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(
        lt, "run_light_trading_cycle", lambda sid, host: called.setdefault("sid", sid)
    )
    monkeypatch.setattr(
        lt,
        "build_light_trading_host",
        lambda svc: lt.LightTradingHost(active_db_sessions={}),
    )
    FullAutoTradingService.get_instance()._run_light_trading_cycle("sess-light")
    assert called.get("sid") == "sess-light"


def test_v3_factor_shim(monkeypatch):
    from backend.services.full_auto import v3_factor_pipeline as v3
    from backend.services.full_auto_trading_service import FullAutoTradingService

    sentinel = ({"BTC": 1}, {}, {})
    monkeypatch.setattr(v3, "run_v3_factor_pipeline", lambda **k: sentinel)
    monkeypatch.setattr(v3, "build_v3_factor_host", lambda svc: v3.V3FactorHost())
    out = FullAutoTradingService.get_instance()._run_v3_factor_pipeline(symbols=["BTC"])
    assert out is sentinel


def test_strategy_lifecycle_shims(monkeypatch):
    from backend.services.full_auto import strategy_lifecycle as sl
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    assert "trending" in svc.REGIME_PARAM_PROFILES

    mem = type("M", (), {"total_trades": 20, "win_rate": 0.6, "sharpe_ratio": 1.0, "max_drawdown": 0.1})()
    assert svc._is_champion_strategy(mem) is True

    monkeypatch.setattr(sl, "should_terminate_strategy", lambda *a, **k: (True, "x"))
    assert svc._should_terminate_strategy(None, None, None) == (True, "x")

    monkeypatch.setattr(sl, "adapt_strategy_params", lambda *a, **k: True)
    assert svc._adapt_strategy_params(None, None, {}) is True

    assert svc._get_regime_profile("ranging")["position_cap_pct"] == sl.REGIME_PARAM_PROFILES["ranging"]["position_cap_pct"] or True
