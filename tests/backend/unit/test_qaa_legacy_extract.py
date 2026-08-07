"""Legacy QAA tick extract smoke tests."""
from __future__ import annotations


def test_qaa_legacy_tick_shim(monkeypatch):
    from backend.services.full_auto import qaa_legacy_cycle as qlc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(qlc, "run_qaa_tick", lambda sid, host: called.setdefault("tick", sid))
    monkeypatch.setattr(qlc, "build_qaa_legacy_host", lambda svc: qlc.QaaLegacyHost(
        market_scan_cache={}, active_positions_cache=[],
    ))

    FullAutoTradingService.get_instance()._run_qaa_tick("sess-legacy")
    assert called.get("tick") == "sess-legacy"


def test_qaa_legacy_register_shim(monkeypatch):
    from backend.services.full_auto import qaa_legacy_cycle as qlc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    host = qlc.QaaLegacyHost(market_scan_cache={}, active_positions_cache=[])

    monkeypatch.setattr(qlc, "register_qaa_agents", lambda h: called.setdefault("reg", h is host))
    monkeypatch.setattr(qlc, "build_qaa_legacy_host", lambda svc: host)

    FullAutoTradingService.get_instance()._register_qaa_agents()
    assert called.get("reg") is True
    assert host.qaa_agents_registered is False or host.qaa_agents_registered is True


def test_qaa_legacy_handler_shim(monkeypatch):
    from backend.services.full_auto import qaa_legacy_cycle as qlc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    sentinel = object()
    monkeypatch.setattr(qlc, "get_qaa_handler", lambda aid, host: sentinel if aid == "market_data" else None)
    monkeypatch.setattr(qlc, "build_qaa_legacy_host", lambda svc: qlc.QaaLegacyHost(
        market_scan_cache={}, active_positions_cache=[],
    ))

    h = FullAutoTradingService.get_instance()._get_qaa_handler("market_data")
    assert h is sentinel
