"""Market scan + qaa v3 tick extract smoke tests."""
from __future__ import annotations


def test_market_scan_shims(monkeypatch):
    from backend.services.full_auto import market_scan_cycle as msc
    from backend.services.full_auto_trading_service import FullAutoTradingService

    host = msc.MarketScanHost(market_scan_cache={}, market_scan_cache_ts=0.0, market_scan_cache_ttl=300.0)
    monkeypatch.setattr(msc, "build_market_scan_host", lambda svc: host)
    monkeypatch.setattr(msc, "run_scan_markets", lambda db, symbols, h: {"BTC": {"price": 1}})
    monkeypatch.setattr(msc, "run_bg_market_scan", lambda symbols, h: None)

    svc = FullAutoTradingService.get_instance()
    assert svc._scan_markets(None, ["BTC"]) == {"BTC": {"price": 1}}
    svc._bg_market_scan(["BTC"])


def test_qaa_v3_tick_shim(monkeypatch):
    from backend.services.full_auto import qaa_v3_tick_cycle as q3
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(q3, "run_qaa_v3_tick", lambda sid, host: called.setdefault("ok", sid))
    monkeypatch.setattr(q3, "build_qaa_v3_tick_host", lambda svc: q3.QaaV3TickHost(
        active_db_sessions={}, market_scan_cache={}, market_scan_cache_ts=0.0,
        active_positions_cache=[], unified_tick_count={},
    ))
    FullAutoTradingService.get_instance()._run_qaa_v3_tick("sess-x")
    assert called.get("ok") == "sess-x"


def test_forced_logs_shim(monkeypatch):
    from backend.services.full_auto import qaa_v3_forced_logs as fl
    from backend.services.full_auto_trading_service import FullAutoTradingService

    called = {}
    monkeypatch.setattr(fl, "write_qaa_v3_forced_decision_logs", lambda **k: called.setdefault("ok", True))
    FullAutoTradingService.get_instance()._write_qaa_v3_forced_decision_logs(
        session_orm_id=1, account_id=2, decisions=[], balance_info={},
        positions_list=[], market_summary={},
    )
    assert called.get("ok") is True
