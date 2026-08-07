"""Smoke tests for FullAuto A–G batch extract (Host + thin shim)."""
from __future__ import annotations


def test_paper_session_helpers_shims(monkeypatch):
    from backend.services.full_auto import paper_session_helpers as mod
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    host = mod.PaperSessionHost()
    monkeypatch.setattr(mod, "build_paper_session_host", lambda s: host)

    called = {}
    monkeypatch.setattr(mod, "paper_auto_unlock_session", lambda *a, **k: called.setdefault("unlock", True))
    assert svc._paper_auto_unlock_session(None, object()) is True
    assert called.get("unlock") is True

    monkeypatch.setattr(mod, "cap_paper_active_strategies", lambda *a, **k: True)
    assert svc._cap_paper_active_strategies(None, object(), []) is True

    monkeypatch.setattr(mod, "get_trade_history", lambda *a, **k: [{"id": 1}])
    assert svc._get_trade_history(None, object()) == [{"id": 1}]

    monkeypatch.setattr(mod, "cleanup_duplicate_strategies", lambda *a, **k: called.setdefault("cleanup", True))
    svc._cleanup_duplicate_strategies(None)
    assert called.get("cleanup") is True


def test_orch_background_shims(monkeypatch):
    from backend.services.full_auto import orch_background as mod
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    host = mod.OrchBackgroundHost(owner=svc)
    monkeypatch.setattr(mod, "build_orch_background_host", lambda s: host)

    monkeypatch.setattr(mod, "build_fast_stability_result", lambda *a, **k: {"ok": True})
    assert FullAutoTradingService._build_fast_stability_result(["BTC"]) == {"ok": True}

    called = {}
    monkeypatch.setattr(mod, "purge_stale_caches", lambda h: called.setdefault("purge", True))
    svc._purge_stale_caches()
    assert called.get("purge") is True

    monkeypatch.setattr(mod, "inject_orch_scheduled_stubs", lambda *a, **k: [{"action": "hold"}])
    assert svc._inject_orch_scheduled_stubs([], {}) == [{"action": "hold"}]

    monkeypatch.setattr(mod, "ensure_orchestrator_bg_running", lambda *a, **k: called.setdefault("bg", True))
    svc._ensure_orchestrator_bg_running("sid", ["BTC"])
    assert called.get("bg") is True


def test_decision_sizing_shims(monkeypatch):
    from backend.services.full_auto import decision_sizing as mod
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    monkeypatch.setattr(mod, "build_decision_sizing_host", lambda s: mod.DecisionSizingHost())

    monkeypatch.setattr(mod, "ai_dynamic_position_pct", lambda *a, **k: 0.05)
    assert svc._ai_dynamic_position_pct(70, 0.02, 1) == 0.05

    monkeypatch.setattr(mod, "apply_tdi_position_advice", lambda *a, **k: (0.06, {"ok": 1}))
    assert svc._apply_tdi_position_advice("BTC", 0.05, 70, 0.02, 1) == (0.06, {"ok": 1})

    monkeypatch.setattr(mod, "resolve_alignment_scale", lambda *a, **k: 0.8)
    assert svc._resolve_alignment_scale("BTC") == 0.8

    monkeypatch.setattr(mod, "resolve_decision_leverage", lambda *a, **k: (10, "ai"))
    assert svc._resolve_decision_leverage({}, "BTC", "mid", {}, None, 1) == (10, "ai")

    monkeypatch.setattr(mod, "resolve_decision_position_pct", lambda *a, **k: (0.07, {}))
    assert svc._resolve_decision_position_pct({}, 70, 0.02, 1, "mid", 0.1, 1000, "ranging", "BTC", "buy") == (0.07, {})

    monkeypatch.setattr(mod, "calibrate_confidence", lambda *a, **k: 66)
    assert svc._calibrate_confidence(70, "buy", "BTC", {}, {}) == 66


def test_tp_sl_gates_shims(monkeypatch):
    from backend.services.full_auto import tp_sl_gates as mod
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    monkeypatch.setattr(mod, "build_tp_sl_gates_host", lambda s: mod.TpSlGatesHost())

    monkeypatch.setattr(mod, "factor_veto_check", lambda *a, **k: "veto")
    assert svc._factor_veto_check(None, "BTC", "buy") == "veto"

    monkeypatch.setattr(mod, "validate_tp_sl_by_nature", lambda *a, **k: (1.1, 0.9))
    assert svc._validate_tp_sl_by_nature("swing", "buy", 1.0, 1.2, 0.8) == (1.1, 0.9)

    monkeypatch.setattr(mod, "compute_dynamic_min_sl", lambda *a, **k: 0.05)
    assert svc._compute_dynamic_min_sl("BTC", "swing", 100.0) == 0.05


def test_live_trading_shims(monkeypatch):
    from backend.services.full_auto import live_trading as mod
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    monkeypatch.setattr(mod, "build_live_trading_host", lambda s: mod.LiveTradingHost())

    monkeypatch.setattr(mod, "live_constitutional_enabled", lambda *a, **k: True)
    assert svc._live_constitutional_enabled(object()) is True

    monkeypatch.setattr(mod, "fetch_live_account_snapshot", lambda *a, **k: {"total_equity": 1})
    assert svc._fetch_live_account_snapshot(None, 1)["total_equity"] == 1

    monkeypatch.setattr(mod, "live_constitutional_pre_trade_check", lambda *a, **k: (True, "ok"))
    assert svc._live_constitutional_pre_trade_check(None, object(), object(), {}) == (True, "ok")

    called = {}
    monkeypatch.setattr(mod, "check_live_constitutional_session_risk", lambda *a, **k: called.setdefault("risk", True))
    svc._check_live_constitutional_session_risk(None, object())
    assert called.get("risk") is True

    monkeypatch.setattr(mod, "execute_live_trade", lambda *a, **k: called.setdefault("live", True))
    svc._execute_live_trade(None, object(), object(), {})
    assert called.get("live") is True


def test_midlong_helpers_shims(monkeypatch):
    from backend.services.full_auto import midlong_helpers as mod
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    monkeypatch.setattr(mod, "build_midlong_helpers_host", lambda s: mod.MidlongHelpersHost())

    monkeypatch.setattr(mod, "resolve_independent_strategy", lambda *a, **k: "strat")
    assert svc._resolve_independent_strategy(None, object(), "BTC", "mid") == "strat"

    monkeypatch.setattr(mod, "try_execute_independent_agent_open", lambda **k: True)
    assert svc._try_execute_independent_agent_open(
        db=None, session=object(), sym="BTC", tier="mid", action="buy",
        confidence=70, trade_nature="swing", market_summary={},
    ) is True

    called = {}
    monkeypatch.setattr(mod, "record_midlong_factor_snapshots", lambda **k: called.setdefault("snap", True))
    svc._record_midlong_factor_snapshots(
        db=None, account_id=1, trade_id=1, symbol="BTC", side="buy", market_data={},
    )
    assert called.get("snap") is True

    monkeypatch.setattr(mod, "persist_independent_scan_log", lambda **k: called.setdefault("log", True))
    svc._persist_independent_scan_log(
        account_id=1, symbol="BTC", tier="mid", trade_nature="swing",
        action="hold", confidence=50, reasoning="x", agent_source="test",
    )
    assert called.get("log") is True

    monkeypatch.setattr(mod, "inject_midlong_indicators", lambda *a, **k: called.setdefault("inj", True))
    svc._inject_midlong_indicators({}, "BTC")
    assert called.get("inj") is True


def test_data_health_shim(monkeypatch):
    from backend.services.full_auto import data_health as mod
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.get_instance()
    host = mod.DataHealthHost()
    monkeypatch.setattr(mod, "build_data_health_host", lambda s: host)
    called = {}
    monkeypatch.setattr(mod, "check_data_health", lambda *a, **k: called.setdefault("ok", True))
    svc._check_data_health(object(), {}, ["BTC"])
    assert called.get("ok") is True


def test_modules_import_and_pure_calls():
    from backend.services.full_auto.decision_sizing import ai_dynamic_position_pct
    from backend.services.full_auto.tp_sl_gates import compute_dynamic_min_sl, validate_tp_sl_by_nature
    from backend.services.full_auto.orch_background import build_fast_stability_result
    from backend.services.full_auto.midlong_helpers import inject_midlong_indicators
    from backend.services.full_auto import (
        paper_session_helpers,
        orch_background,
        decision_sizing,
        tp_sl_gates,
        live_trading,
        midlong_helpers,
        data_health,
    )

    assert ai_dynamic_position_pct(80, 0.01, 0) >= 0.04
    assert compute_dynamic_min_sl("BTC", "swing", 100.0) >= 0.05
    tp, sl = validate_tp_sl_by_nature("swing", "buy", 100.0, 101.0, 99.0, symbol="BTC")
    assert tp and sl
    pack = build_fast_stability_result(["BTC", "ETH"], trigger="timeout", timeout_s=30)
    assert pack["master_decision"]["decisions"]
    inject_midlong_indicators({}, "BTC")  # no-op safe

    assert callable(paper_session_helpers.build_paper_session_host)
    assert callable(orch_background.build_orch_background_host)
    assert callable(decision_sizing.build_decision_sizing_host)
    assert callable(tp_sl_gates.build_tp_sl_gates_host)
    assert callable(live_trading.build_live_trading_host)
    assert callable(midlong_helpers.build_midlong_helpers_host)
    assert callable(data_health.build_data_health_host)
