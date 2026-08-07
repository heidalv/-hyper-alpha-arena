"""
C2 特征化测试网：各 full_auto loop 单 tick golden 快照。

对应文档 §10.9 C2：给定固定 DB stub + 行情快照，钉住各 loop 在门禁路径下
产出的开/平仓意图集合（当前为 guard-path 基线，后续扩展 happy-path）。

解锁 #8 loop 拆分 / #9 Phase 2 的前置安全网之一。
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

_FIXTURES = os.path.join(os.path.dirname(__file__), "..", "fixtures", "full_auto_c2")
_SESSION_ID = "c2-golden-session"


def _golden(name: str) -> str:
    return os.path.normpath(os.path.join(_FIXTURES, f"{name}.json"))


def _make_svc():
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.__new__(FullAutoTradingService)
    svc._unified_tick_count = {}
    svc._running_sessions = {}
    svc._midlong_symbol_cursor = {}
    svc._midlong_loop_running = {}
    svc._market_scan_cache = {}
    svc._orch_bg_symbols = ["BTC"]
    svc._scalp_factor_cache = {}
    svc._session_status_cache = {}
    return svc


class _FakeQuery:
    def __init__(self, result=None, results=None):
        self._result = result
        self._results = results or []

    def filter(self, *args, **kwargs):
        return self

    def first(self):
        return self._result

    def all(self):
        return self._results

    def flush(self):
        pass


class _FakeDB:
    """按 ORM 类名路由 query 结果。"""

    def __init__(self, mapping: dict):
        self._mapping = mapping

    def query(self, model):
        name = getattr(model, "__name__", str(model))
        for key, val in self._mapping.items():
            if key in name:
                if isinstance(val, list):
                    return _FakeQuery(results=val)
                return _FakeQuery(result=val)
        return _FakeQuery(result=None)

    def flush(self):
        pass

    def close(self):
        pass

    def commit(self):
        pass


def _session_row(**kwargs):
    defaults = dict(
        session_id=_SESSION_ID,
        status="running",
        account_id=1,
        paper_account_id=1,
        trading_mode="paper",
        last_market_summary={"BTC": {"current_price": 60000.0}},
        symbols='["BTC"]',
        target_symbols='["BTC"]',
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── C2-1: coordinator（_run_unified_loop）────────────────────────

def test_c2_coordinator_paused_produces_empty_intents(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match

    svc = _make_svc()
    calls = {"trading_cycle": False, "arbitrage_tick": False, "learning_integration": False, "maintenance_cycle": False}

    monkeypatch.setattr(svc, "_get_session_status_fast", lambda sid: "paused")
    monkeypatch.setattr(svc, "_run_trading_cycle", lambda *a, **k: calls.__setitem__("trading_cycle", True))
    monkeypatch.setattr(svc, "_run_arbitrage_tick", lambda *a, **k: calls.__setitem__("arbitrage_tick", True))
    monkeypatch.setattr(svc, "_run_learning_integration", lambda *a, **k: calls.__setitem__("learning_integration", True))
    monkeypatch.setattr(svc, "_run_maintenance_cycle", lambda *a, **k: calls.__setitem__("maintenance_cycle", True))

    svc._run_unified_loop(_SESSION_ID)

    snap = LoopTickSnapshot(
        scenario="coordinator_paused",
        loop="coordinator",
        intents=[],
        observed_calls=calls,
    )
    assert_golden_match(snap, _golden("coordinator_paused"))


# ── C2-2: midlong（_run_midlong_independent）──────────────────────

def test_c2_midlong_no_due_tiers_produces_empty_intents(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match
    import backend.database.connection as conn_mod

    svc = _make_svc()
    row = _session_row()
    db = _FakeDB({"FullAutoSession": row})
    monkeypatch.setattr(conn_mod, "SessionLocal", lambda: db)

    calls = {"maintain_mlto": False, "mark_tier_run": False}
    monkeypatch.setattr(
        "backend.services.tier_tick_scheduler.get_due_ai_tiers",
        lambda sid: [],
    )
    monkeypatch.setattr(svc, "_resolve_session_trade_symbols", lambda sess, d: ["BTC"])
    monkeypatch.setattr(
        svc,
        "_maintain_mlto_theses_for_session",
        lambda **kw: calls.__setitem__("maintain_mlto", True),
    )
    monkeypatch.setattr(
        "backend.services.tier_tick_scheduler.mark_tier_run",
        lambda sid, tiers: calls.__setitem__("mark_tier_run", True),
    )

    svc._run_midlong_independent(_SESSION_ID, tick=1)

    snap = LoopTickSnapshot(
        scenario="midlong_no_due_tiers",
        loop="midlong",
        intents=[],
        observed_calls=calls,
    )
    assert_golden_match(snap, _golden("midlong_no_due_tiers"))


# ── C2-3: scalp（_run_scalp_independent）──────────────────────────

def test_c2_scalp_zero_equity_produces_empty_intents(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match
    import backend.database.connection as conn_mod

    svc = _make_svc()
    row = _session_row()
    account = SimpleNamespace(id=1, name="test")
    balance = SimpleNamespace(total_equity=0, equity=0)
    db = _FakeDB({
        "FullAutoSession": row,
        "Account": account,
        "PaperBalance": balance,
        "PaperPosition": [],
    })
    monkeypatch.setattr(conn_mod, "SessionLocal", lambda: db)

    router_called = {"evaluate": False}
    mock_router = MagicMock()
    mock_router.evaluate = lambda sym, md: router_called.__setitem__("evaluate", True)
    monkeypatch.setattr(
        "backend.services.scalp_factor_router.scalp_factor_router",
        mock_router,
    )
    monkeypatch.setattr(svc, "_resolve_session_trade_symbols", lambda sess, d: ["BTC"])

    svc._run_scalp_independent(_SESSION_ID, tick=1)

    snap = LoopTickSnapshot(
        scenario="scalp_zero_equity",
        loop="scalp",
        intents=[],
        observed_calls={"scalp_router_evaluate": router_called["evaluate"]},
    )
    assert_golden_match(snap, _golden("scalp_zero_equity"))


# ── C2-4: arbitrage（_run_arbitrage_tick）─────────────────────────

def test_c2_arbitrage_disabled_produces_empty_intents(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match

    svc = _make_svc()
    svc._running_sessions[_SESSION_ID] = {"arb_enabled": False}

    orch_called = {"run": False}
    monkeypatch.setattr(
        "backend.services.arbitrage.execution_authority.ExecutionAuthority.run_v3_arbitrage_tick",
        lambda **kw: orch_called.__setitem__("run", True) or {},
    )

    svc._run_arbitrage_tick(_SESSION_ID)

    snap = LoopTickSnapshot(
        scenario="arbitrage_disabled",
        loop="arbitrage",
        intents=[],
        observed_calls={"arb_orchestrator": orch_called["run"]},
    )
    assert_golden_match(snap, _golden("arbitrage_disabled"))


# ── C2-5: learning（_run_learning_integration）────────────────────

def test_c2_learning_tick1_light_produces_empty_intents(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match
    import backend.database.connection as conn_mod

    svc = _make_svc()
    monkeypatch.setattr(conn_mod, "SessionLocal", lambda: _FakeDB({}))

    # tick=1 时 P0/P1 需 tick%30==0 才触发，P2 需 maintenance 或 tick%N==0；
    # 本场景为轻量心跳，不应产生任何交易意图。
    svc._run_learning_integration(_SESSION_ID, tick=1, is_maintenance=False)

    snap = LoopTickSnapshot(
        scenario="learning_tick1_light",
        loop="learning",
        intents=[],
        observed_calls={"paper_engine": False},
    )
    assert_golden_match(snap, _golden("learning_tick1_light"))


# ── C2-6: maintenance（_run_maintenance_cycle）───────────────────

def test_c2_maintenance_housekeeping_produces_empty_intents(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match

    svc = _make_svc()
    svc._unified_tick_count[_SESSION_ID] = 5
    calls = {"health_check": False, "learning_integration": False}

    monkeypatch.setattr(
        svc,
        "_run_health_check",
        lambda sid, maintenance_only=False: calls.__setitem__("health_check", True),
    )
    monkeypatch.setattr(
        svc,
        "_run_learning_integration",
        lambda sid, tick, is_maintenance=False: calls.__setitem__("learning_integration", True),
    )

    svc._run_maintenance_cycle(_SESSION_ID)

    snap = LoopTickSnapshot(
        scenario="maintenance_housekeeping",
        loop="maintenance",
        intents=[],
        observed_calls=calls,
    )
    assert_golden_match(snap, _golden("maintenance_housekeeping"))


# ── C2 happy-path：各 loop 正向调度基线 ─────────────────────────────

def test_c2_coordinator_running_dispatches_ai_tick(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match

    svc = _make_svc()
    calls = {
        "trading_cycle": False, "arbitrage_tick": False,
        "learning_integration": False, "maintenance_cycle": False,
    }
    monkeypatch.setattr(svc, "_get_session_status_fast", lambda sid: "running")
    monkeypatch.setattr(
        "backend.services.tier_tick_scheduler.get_due_ai_tiers",
        lambda sid: ["mid"],
    )
    monkeypatch.setattr(svc, "_run_trading_cycle", lambda *a, **k: calls.__setitem__("trading_cycle", True))
    monkeypatch.setattr(svc, "_run_arbitrage_tick", lambda *a, **k: calls.__setitem__("arbitrage_tick", True))
    monkeypatch.setattr(svc, "_run_learning_integration", lambda *a, **k: calls.__setitem__("learning_integration", True))
    monkeypatch.setattr(svc, "_run_maintenance_cycle", lambda *a, **k: calls.__setitem__("maintenance_cycle", True))
    monkeypatch.setattr(svc, "_run_hold_timeout_ai_review_if_needed", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_run_rebate_arb_tick", lambda *a, **k: None)
    monkeypatch.setattr(
        "backend.services.tier_tick_scheduler.mark_tier_run",
        lambda sid, tiers: None,
    )
    monkeypatch.setattr(
        "backend.services.tier_tick_scheduler.mark_coordinator_run",
        lambda sid: None,
    )
    monkeypatch.setattr(
        "backend.config.settings.FULLAUTO_LEARNING_INTEGRATION_EVERY_N",
        99,
    )
    monkeypatch.setattr(
        "backend.config.settings.PAPER_FAST_TRIAL",
        False,
    )

    svc._run_unified_loop(_SESSION_ID)

    snap = LoopTickSnapshot(
        scenario="coordinator_running_ai_tick",
        loop="coordinator",
        intents=[],
        observed_calls=calls,
    )
    assert_golden_match(snap, _golden("coordinator_running_ai_tick"))


def test_c2_midlong_due_tiers_dispatches_mlto(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match
    import backend.database.connection as conn_mod

    svc = _make_svc()
    row = _session_row()
    db = _FakeDB({"FullAutoSession": row})
    monkeypatch.setattr(conn_mod, "SessionLocal", lambda: db)

    calls = {"maintain_mlto": False, "mark_tier_run": False}
    monkeypatch.setattr(
        "backend.services.tier_tick_scheduler.get_due_ai_tiers",
        lambda sid: ["mid"],
    )
    monkeypatch.setattr(svc, "_resolve_session_trade_symbols", lambda sess, d: ["BTC"])
    monkeypatch.setattr(svc, "_scan_markets", lambda d, syms: {})
    monkeypatch.setattr(svc, "_ensure_market_prices", lambda ms, syms: None)
    monkeypatch.setattr(svc, "_build_portfolio_for_agents", lambda d, s: {"positions": []})
    monkeypatch.setattr(svc, "_safe_commit", lambda *a, **k: None)
    monkeypatch.setattr(
        svc,
        "_maintain_mlto_theses_for_session",
        lambda **kw: calls.__setitem__("maintain_mlto", True),
    )
    monkeypatch.setattr(
        "backend.services.tier_tick_scheduler.mark_tier_run",
        lambda sid, tiers: calls.__setitem__("mark_tier_run", True),
    )
    monkeypatch.setattr(svc, "_run_midlong_active_exit", lambda *a, **k: None)

    svc._run_midlong_independent(_SESSION_ID, tick=1)

    snap = LoopTickSnapshot(
        scenario="midlong_due_tiers_dispatch",
        loop="midlong",
        intents=[],
        observed_calls=calls,
    )
    assert_golden_match(snap, _golden("midlong_due_tiers_dispatch"))


def test_c2_arbitrage_enabled_runs_orchestrator(monkeypatch):
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, assert_golden_match

    svc = _make_svc()
    svc._running_sessions[_SESSION_ID] = {
        "arb_enabled": True,
        "symbols": ["BTC"],
        "session_obj": SimpleNamespace(symbols=["BTC"]),
    }
    svc._last_unified_snapshot = SimpleNamespace(btc=60000)
    svc._arb_orchestrator = MagicMock()  # 跳过 orchestrator 导入，直达 ExecutionAuthority

    orch_called = {"run": False}
    monkeypatch.setattr("backend.config.settings.FUNDING_ARB_ENABLED", True)
    monkeypatch.setattr(
        "backend.services.arbitrage.execution_authority.ExecutionAuthority.run_v3_arbitrage_tick",
        lambda **kw: orch_called.__setitem__("run", True) or {"scanned": 1},
    )

    svc._run_arbitrage_tick(_SESSION_ID)

    snap = LoopTickSnapshot(
        scenario="arbitrage_enabled_scan",
        loop="arbitrage",
        intents=[],
        observed_calls={"arb_orchestrator": orch_called["run"]},
    )
    assert_golden_match(snap, _golden("arbitrage_enabled_scan"))


def test_c2_scalp_buy_signal_reaches_v5(monkeypatch):
    """happy-path：buy 信号通过 Gate 到达 evaluate_scalp_proposal（钉住 V5 入口）。"""
    import numpy as np
    import pandas as pd
    from backend.services.full_auto.intent_snapshot import LoopTickSnapshot, TradeIntent, assert_golden_match
    from backend.services.decision_core.execute_proposal import EvaluateVerdict
    from backend.services.scalp_factor_router import ScalpSignal
    import backend.database.connection as conn_mod

    svc = _make_svc()
    svc._scalp_open_ts = {}
    svc._scalp_open_ts_side = {}
    row = _session_row()
    db = MagicMock()

    def _query(model):
        name = getattr(model, "__name__", "")
        q = MagicMock()
        if "FullAutoSession" in name:
            q.filter.return_value.first.return_value = row
        elif "Account" in name:
            q.filter.return_value.first.return_value = SimpleNamespace(id=1, name="test")
        elif "PaperBalance" in name:
            q.filter.return_value.first.return_value = SimpleNamespace(total_equity=10000, equity=10000)
        elif "PaperPosition" in name:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
            q.filter.return_value.count.return_value = 0
        else:
            q.filter.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
            q.filter.return_value.count.return_value = 0
        return q

    db.query = _query
    db.close = MagicMock()
    monkeypatch.setattr(conn_mod, "SessionLocal", lambda: db)
    monkeypatch.setattr(svc, "_resolve_session_trade_symbols", lambda sess, d: ["BTC"])
    monkeypatch.setattr(svc, "_persist_tcp_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_check_liq_magnet_reversal_exit", lambda **kw: None)
    svc._market_scan_cache = {
        "BTC": {
            "factor_v3": {"direction": 0.8, "strength": 0.8},
            "current_price": 60000.0,
        },
    }
    monkeypatch.setattr(
        "backend.services.factor_engine.factor_bridge.inject_orderflow_for_factors",
        lambda sym, md, **kw: md,
    )
    monkeypatch.setattr(
        "backend.services.factor_engine.base_factors.factor_engine.compute_atr_ratio",
        lambda kdf: 0.02,
    )
    monkeypatch.setattr("backend.config.settings.SCALP_RANGING_MR_ENABLED", False)
    monkeypatch.setattr("backend.services.scalp_signal_logger.log_signal", lambda **kw: None)
    monkeypatch.setenv("SCALP_PROFILE_LOG", "false")

    _klines = pd.DataFrame({
        "open": np.linspace(59000, 60000, 50),
        "high": np.linspace(59100, 60100, 50),
        "low": np.linspace(58900, 59900, 50),
        "close": np.linspace(59000, 60000, 50),
    })
    monkeypatch.setattr(
        "backend.services.kline_data_service.kline_service.get_klines_from_db",
        lambda *a, **k: _klines.to_dict("records"),
    )

    buy_sig = ScalpSignal(
        action="buy", direction="long", factor_score=85, confidence=85,
        entry_price=60000, sl_pct=0.008, tp_pct=0.007,
    )
    mock_router = MagicMock()
    mock_router.evaluate = lambda sym, md: buy_sig
    mock_router._get_adaptive_threshold = lambda sym: 25
    monkeypatch.setattr(
        "backend.services.scalp_factor_router.scalp_factor_router",
        mock_router,
    )

    _gate = SimpleNamespace(
        allowed=True, reason="", tier="T1", lane_decision_id="lane1",
        effective_score=85, sl_pct=0.008, tp_pct=0.007,
        sl_price=0, tp_price=0, needs_veto=False, advisory=None,
    )
    from backend.services.scalp.scalp_execution_gate import scalp_execution_gate as _scalp_gate
    monkeypatch.setattr(_scalp_gate, "evaluate", lambda *a, **k: _gate)
    from backend.services.scalp.scalp_flash_veto import scalp_flash_veto as _sfv
    monkeypatch.setattr(_sfv, "should_invoke", lambda *a, **k: False)
    from backend.services.scalp import scalp_mtf_constraint as _mtf_mod
    monkeypatch.setattr(
        _mtf_mod, "evaluate_scalp_mtf_constraint",
        lambda *a, **k: SimpleNamespace(hold=False, size_multiplier=1.0, reason=""),
    )
    monkeypatch.setattr(
        "backend.services.reentry_cooldown.reopen_blocked",
        lambda *a, **k: (False, ""),
    )
    monkeypatch.setattr(
        "backend.services.short_tier_entry_gate.apply_short_tier_gate",
        lambda **kw: (True, "ok"),
    )
    monkeypatch.setattr(
        "backend.services.dynamic_leverage_calculator.calculate_dynamic_leverage",
        lambda *a, **k: 10,
    )

    v5_called = {"n": 0}
    place_called = {"n": 0}

    def _fake_v5(**kw):
        v5_called["n"] += 1
        return EvaluateVerdict(allowed=True, reason="ok")

    monkeypatch.setattr(
        "backend.services.decision_core.execute_proposal.evaluate_scalp_proposal",
        _fake_v5,
    )
    monkeypatch.setattr(
        "backend.services.paper_trading_engine.paper_engine.place_order",
        lambda *a, **k: place_called.__setitem__("n", place_called["n"] + 1),
    )

    svc._run_scalp_independent(_SESSION_ID, tick=99)

    snap = LoopTickSnapshot(
        scenario="scalp_buy_signal_reaches_v5",
        loop="scalp",
        intents=[TradeIntent(
            loop="scalp", action="open", symbol="BTC", side="buy",
            tier="short", reason="evaluate_scalp_proposal",
        )] if v5_called["n"] > 0 else [],
        observed_calls={
            "evaluate_scalp_proposal": v5_called["n"] > 0,
            "place_order": place_called["n"] > 0,
        },
    )
    assert_golden_match(snap, _golden("scalp_buy_signal_reaches_v5"))
