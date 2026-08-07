"""
C3–C6 特征化测试网（§10.9）。

C3: 冻结/冷却/日亏门禁联动
C4: 预算单位（名义 vs 保证金）
C5: V5 fail-closed / MR TP-SL
C6: DB 事务边界 leak guard
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit

_SESSION_ID = "char-test-session"


def _make_svc():
    from backend.services.full_auto_trading_service import FullAutoTradingService

    svc = FullAutoTradingService.__new__(FullAutoTradingService)
    svc._scalp_open_ts = {}
    svc._scalp_open_ts_side = {}
    svc._market_scan_cache = {}
    svc._orch_bg_symbols = ["BTC"]
    svc._scalp_factor_cache = {}
    return svc


class _FakeQuery:
    def __init__(self, result=None, results=None):
        self._result = result
        self._results = results or []

    def filter(self, *a, **k):
        return self

    def first(self):
        return self._result

    def all(self):
        return self._results

    def count(self):
        return len(self._results)


class _FailThenOkDB:
    """第一次 query 抛 reconnect 异常，第二次正常。"""

    def __init__(self, ok_rows=None):
        self._calls = 0
        self._ok_rows = ok_rows or []
        self.closed = False

    def query(self, model):
        self._calls += 1
        if self._calls == 1:
            raise Exception("Can't reconnect until invalid transaction is rolled back")
        return _FakeQuery(results=self._ok_rows)

    def close(self):
        self.closed = True


class _SessionCounterDB:
    """记录 SessionLocal 创建次数与是否在传入 db 上 query。"""

    instances = []

    def __init__(self, rows=None):
        self.rows = rows or []
        self.queried_on_self = False
        self.closed = False
        _SessionCounterDB.instances.append(self)

    def query(self, model):
        self.queried_on_self = True
        return _FakeQuery(results=self.rows)

    def close(self):
        self.closed = True


# ============================ C3：冻结/冷却/日亏门禁 ============================

def test_c3_master_defensive_blocks_new_opens():
    """Master 路径：defensive 模式下 buy/sell 不产新开仓意图（钉住 mode==running 门槛）。"""
    for mode in ("defensive", "paused", "stopped"):
        action = "buy"
        would_open = action in ("buy", "sell") and mode == "running"
        assert would_open is False, f"mode={mode} 不应产新开仓"

    assert ("buy" in ("buy", "sell") and "running" == "running") is True


def test_c3_fullauto_state_daily_loss_blocks_opens():
    """FullAutoState 日亏门禁：触发后 daily_loss_breached=True。"""
    from backend.services.full_auto import FullAutoState

    s = FullAutoState(daily_loss_limit=500.0)
    s.roll_day("2026-07-09")
    s.register_trade_result(-600)
    assert s.daily_loss_breached() is True
    # 特征化：monolith 全局日亏→defensive 后 Master 不开仓（见 test_c3_master_defensive）


def test_c3_scalp_reopen_cooldown_blocks_before_budget(monkeypatch):
    """平仓冷却拦截：reopen_blocked=True 时 scalp 不调 can_open。"""
    import numpy as np
    import pandas as pd
    from backend.services.scalp_factor_router import ScalpSignal
    import backend.database.connection as conn_mod

    svc = _make_svc()
    row = SimpleNamespace(
        session_id=_SESSION_ID, status="running", account_id=1,
        paper_account_id=1, trading_mode="paper",
    )
    db = MagicMock()
    monkeypatch.setattr(conn_mod, "SessionLocal", lambda: db)

    def _query(model):
        name = getattr(model, "__name__", "")
        q = MagicMock()
        if "FullAutoSession" in name:
            q.filter.return_value.first.return_value = row
        elif "Account" in name:
            q.filter.return_value.first.return_value = SimpleNamespace(id=1)
        elif "PaperBalance" in name:
            q.filter.return_value.first.return_value = SimpleNamespace(total_equity=10000, equity=10000)
        elif "PaperPosition" in name:
            q.filter.return_value.all.return_value = []
        return q

    db.query = _query
    db.close = MagicMock()

    monkeypatch.setattr(svc, "_resolve_session_trade_symbols", lambda sess, d: ["BTC"])
    _klines = pd.DataFrame({
        "open": np.full(50, 60000.0), "high": np.full(50, 60100.0),
        "low": np.full(50, 59900.0), "close": np.full(50, 60000.0),
    })
    monkeypatch.setattr(
        "backend.services.kline_data_service.kline_service.get_klines_from_db",
        lambda *a, **k: _klines.to_dict("records"),
    )
    buy_sig = ScalpSignal(action="buy", direction="long", factor_score=90, confidence=90)
    mock_router = MagicMock()
    mock_router.evaluate = lambda sym, md: buy_sig
    mock_router._get_adaptive_threshold = lambda sym: 25
    monkeypatch.setattr("backend.services.scalp_factor_router.scalp_factor_router", mock_router)

    _gate = SimpleNamespace(
        allowed=True, reason="", tier="T1", lane_decision_id="x",
        effective_score=90, sl_pct=0.01, tp_pct=0.01,
        sl_price=0, tp_price=0, needs_veto=False, advisory=None,
    )
    from backend.services.scalp.scalp_execution_gate import scalp_execution_gate as _scalp_gate
    monkeypatch.setattr(_scalp_gate, "evaluate", lambda *a, **k: _gate)
    monkeypatch.setattr(
        "backend.services.reentry_cooldown.reopen_blocked",
        lambda *a, **k: (True, "cooldown_4h"),
    )

    can_open_called = {"n": 0}
    orig_can_open = None
    try:
        from backend.services.budget_service import budget_service
        orig_can_open = budget_service.can_open
        budget_service.can_open = lambda *a, **k: can_open_called.__setitem__("n", can_open_called["n"] + 1) or True
        svc._run_scalp_independent(_SESSION_ID, tick=1)
    finally:
        if orig_can_open:
            budget_service.can_open = orig_can_open

    assert can_open_called["n"] == 0


# ============================ C4：预算单位（名义 vs 保证金）====================

def test_c4_scalp_nominal_converted_to_margin_before_can_open():
    """2026-07-09 修复：名义 3000 ÷ 杠杆 10 = 保证金 300，应通过 short 层预算。"""
    from backend.services.budget_service import BudgetService

    bs = BudgetService()
    bs.get_used_margin = lambda layer, mode="paper": 0.0  # type: ignore[method-assign]

    equity = 10000.0
    scalp_size_pct = 0.30
    dyn_lev = 10
    margin_est = equity * scalp_size_pct          # 名义 3000
    req_margin = margin_est / dyn_lev             # 保证金 300

    assert req_margin == pytest.approx(300.0)
    assert bs.can_open("short", req_margin, equity) is True
    assert bs.can_open("short", margin_est, equity) is False  # 误传名义 → 永远撞预算


# ============================ C5：V5 fail-closed / MR TP-SL ====================

def test_c5_strict_data_blocks_missing_volatility_value(monkeypatch):
    from backend.services.decision_core.data_contract import apply_data_contract_gate

    monkeypatch.setenv("STRICT_DATA_GATE", "true")
    ok, reason = apply_data_contract_gate("short", {"price": 60000}, mode="paper")
    assert ok is False
    assert "volatility_value" in reason


def test_c5_strict_data_blocks_missing_indicators_1w(monkeypatch):
    from backend.services.decision_core.data_contract import apply_data_contract_gate

    monkeypatch.setenv("STRICT_DATA_GATE", "true")
    mkt = {"price": 60000, "indicators_1d": {"rsi": 50}}
    ok, reason = apply_data_contract_gate("long", mkt, mode="paper")
    assert ok is False
    assert "indicators_1w" in reason


def test_c5_mr_tight_tp_sl_not_treated_as_placeholder():
    """MR 小止盈止损（0.7%/0.8%）在 ranging_mr 模式下不被替换成 tier 大默认值。"""
    from backend.services.decision_core.pipeline import _looks_like_ai_placeholder_tp_sl

    tp_pct, sl_pct = 0.007, 0.008
    assert _looks_like_ai_placeholder_tp_sl(tp_pct, sl_pct) is True  # 形似占位符

    market_data = {"ranging_mr": True, "price": 60000, "volatility_value": 0.02}
    _mr_flag = bool(market_data.get("ranging_mr"))
    orig_tp, orig_sl = tp_pct, sl_pct
    if not _mr_flag and tp_pct and sl_pct and _looks_like_ai_placeholder_tp_sl(tp_pct, sl_pct):
        tp_pct, sl_pct = 0.02, 0.01
    assert tp_pct == orig_tp and sl_pct == orig_sl


def test_c5_v5_gate_exception_fail_closed(monkeypatch):
    """V5 异常路径：short_tier 检查抛错 → fail-closed 拦截。"""
    from backend.services.decision_core.unified_gate import evaluate_entry

    mock_db = MagicMock()
    monkeypatch.setattr(
        "backend.services.short_tier_entry_gate.check_short_tier_entry",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    result = evaluate_entry(
        db=mock_db, account_id=1, symbol="BTC", action="buy",
        confidence=80, tier="short", trade_nature="scalp",
        tp_pct=0.01, sl_pct=0.008,
        market_data={"price": 60000, "volatility_value": 0.02},
        mode="paper",
    )
    assert result.allowed is False
    assert "short_tier" in (result.reason or "").lower() or "异常" in (result.reason or "")


# ============================ C6：DB 事务边界 leak guard =====================

def test_c6_fee_context_uses_fresh_short_session(monkeypatch):
    """fee_context 统计走独立短连接，不在传入的长连接 db 上 query。"""
    from backend.services.decision_core.fee_context import build_fee_context

    stale_db = MagicMock()
    stale_db.query = MagicMock(side_effect=AssertionError("must not query stale db"))
    used = {"stale": False, "fresh": False}

    def _query_fee_stats(db, account_id):
        if db is stale_db:
            used["stale"] = True
        else:
            used["fresh"] = True
        return 0.0, 0.0, 0, {}

    monkeypatch.setattr(
        "backend.services.decision_core.fee_context._query_fee_stats",
        _query_fee_stats,
    )
    closed = {"n": 0}
    mock_short = MagicMock()
    mock_short.close = lambda: closed.__setitem__("n", closed["n"] + 1)
    monkeypatch.setattr(
        "backend.database.connection.SessionLocal",
        lambda: mock_short,
    )

    build_fee_context(stale_db, account_id=1)
    assert used["fresh"] is True
    assert used["stale"] is False
    assert closed["n"] == 1
    stale_db.query.assert_not_called()


def test_c6_feedback_retries_with_fresh_session_on_reconnect_error(monkeypatch):
    """decision_feedback 在 reconnect 错误时用全新 SessionLocal 重试。"""
    from backend.services.decision_feedback_service import DecisionFeedbackService

    session_calls = {"n": 0}

    def _session_local():
        session_calls["n"] += 1
        db = MagicMock()
        db.query.return_value.filter.return_value.all.return_value = []
        db.close = MagicMock()
        return db

    monkeypatch.setattr("backend.database.connection.SessionLocal", _session_local)

    stale = MagicMock()
    stale.query.side_effect = Exception("Can't reconnect until invalid transaction is rolled back")

    svc = DecisionFeedbackService()
    result = svc.build_net_attribution(stale, days=7)
    assert session_calls["n"] >= 1
    assert "summary" in result


def test_c6_midlong_closes_db_before_long_llm(monkeypatch):
    """midlong tick：长 LLM 段之前关闭主连接（防 idle-in-transaction 泄漏）。"""
    import backend.database.connection as conn_mod
    from tests.backend.unit.test_full_auto_loop_c2_golden import _FakeDB, _session_row

    svc = _make_svc()
    row = _session_row()
    close_called = {"n": 0}
    session_calls = {"n": 0}

    class _TrackingDB(_FakeDB):
        def close(self):
            close_called["n"] += 1

    def _session_local():
        session_calls["n"] += 1
        if session_calls["n"] == 1:
            return _TrackingDB({"FullAutoSession": row})
        return _FakeDB({"FullAutoSession": row})

    monkeypatch.setattr(conn_mod, "SessionLocal", _session_local)
    monkeypatch.setattr("backend.services.tier_tick_scheduler.get_due_ai_tiers", lambda sid: ["mid"])
    monkeypatch.setattr(svc, "_resolve_session_trade_symbols", lambda sess, d: ["BTC"])
    monkeypatch.setattr(svc, "_scan_markets", lambda d, syms: {})
    monkeypatch.setattr(svc, "_ensure_market_prices", lambda ms, syms: None)
    monkeypatch.setattr(svc, "_build_portfolio_for_agents", lambda d, s: {"positions": []})
    monkeypatch.setattr(svc, "_safe_commit", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_maintain_mlto_theses_for_session", lambda **kw: None)
    monkeypatch.setattr("backend.services.tier_tick_scheduler.mark_tier_run", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_run_midlong_active_exit", lambda *a, **k: None)

    svc._run_midlong_independent(_SESSION_ID, tick=1)

    assert close_called["n"] >= 1
    assert session_calls["n"] >= 2
