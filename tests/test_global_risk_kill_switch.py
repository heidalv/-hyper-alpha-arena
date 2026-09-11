# -*- coding: utf-8 -*-
"""v3 Phase 0 F1/F2 停机与账本单测。

覆盖：
- 会话回撤硬闸：check_global_risk / drawdown_limit_breached 返回稳定原因码；
- paper_auto_unlock_session 不得解开 drawdown_limit；
- 账户级日手续费预算门：超预算只拦新开；
- 短线影子模式开关解析（fail-closed 语义）；
- tier 日亏预算 SQL 含手续费（口径守卫）。
全部为纯逻辑测试，不连数据库。
"""
from __future__ import annotations

import os
import types
from unittest.mock import MagicMock

import pytest


def _session(**kw):
    s = types.SimpleNamespace(
        session_id="sess-test", status="running", pause_reason=None,
        max_total_drawdown_pct=0.30, current_drawdown=0.0, peak_balance=1000.0,
        last_market_summary={}, account_id=1, trading_mode="paper",
        active_strategy_ids=[], terminated_strategy_ids=[], symbols=[], auto_coin_symbols=[],
        total_pnl=0.0,
    )
    for k, v in kw.items():
        setattr(s, k, v)
    return s


# ── 回撤硬闸 ────────────────────────────────────────────────────────────────

def test_drawdown_limit_breached_pure():
    from backend.services.full_auto.symbol_risk import drawdown_limit_breached, DRAWDOWN_LIMIT_CODE

    assert drawdown_limit_breached(_session(current_drawdown=0.20)) is None
    hit = drawdown_limit_breached(_session(current_drawdown=0.35))
    assert hit and hit.startswith(DRAWDOWN_LIMIT_CODE)
    # 阈值缺省 0.30
    assert drawdown_limit_breached(_session(current_drawdown=0.31, max_total_drawdown_pct=None))


def test_check_global_risk_returns_stable_codes():
    from backend.services.full_auto.symbol_risk import (
        check_global_risk, DRAWDOWN_LIMIT_CODE, SENTIMENT_EXTREME_CODE,
    )
    host = MagicMock()
    host.defensive_entered_at = {}
    db = MagicMock()

    assert check_global_risk(db, _session(current_drawdown=0.10), host) is None
    r = check_global_risk(db, _session(current_drawdown=0.61), host)
    assert r.startswith(DRAWDOWN_LIMIT_CODE)
    r2 = check_global_risk(db, _session(last_market_summary={"BTC": {"sentiment_index": 5}}), host)
    assert r2.startswith(SENTIMENT_EXTREME_CODE)
    # 回撤优先于情绪
    r3 = check_global_risk(db, _session(current_drawdown=0.5, last_market_summary={"BTC": {"sentiment_index": 5}}), host)
    assert r3.startswith(DRAWDOWN_LIMIT_CODE)


def test_paper_auto_unlock_never_unlocks_drawdown_limit():
    from backend.services.full_auto.paper_session_helpers import paper_auto_unlock_session

    host = MagicMock()
    host.paper_loss_locks_disabled.return_value = True
    host.symbol_frozen_set = {"sess-test": {"BTC"}}
    host.symbol_frozen_tiers = {"sess-test": {"BTC": ["short"]}}
    host.defensive_entered_at = {}
    host.recovery_until = {}
    host.strat_pause_meta = {}
    db = MagicMock()

    sess = _session(status="paused", pause_reason="drawdown_limit")
    changed = paper_auto_unlock_session(db, sess, host)
    assert changed is False
    assert sess.status == "paused"
    assert sess.pause_reason == "drawdown_limit"
    # 连 symbol 冻结集也不能被清空
    assert host.symbol_frozen_set["sess-test"] == {"BTC"}


def test_paper_auto_unlock_still_unlocks_ordinary_loss_lock():
    """回归：普通亏损锁（circuit_breaker）仍按原逻辑自动解开，避免误伤。"""
    from backend.services.full_auto.paper_session_helpers import paper_auto_unlock_session

    host = MagicMock()
    host.paper_loss_locks_disabled.return_value = True
    host.symbol_frozen_set = {}
    host.symbol_frozen_tiers = {}
    host.defensive_entered_at = {}
    host.recovery_until = {}
    host.strat_pause_meta = {}
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.query.return_value.filter.return_value.all.return_value = []

    sess = _session(status="paused", pause_reason="circuit_breaker")
    paper_auto_unlock_session(db, sess, host)
    assert sess.status == "running"


# ── 手续费预算 ───────────────────────────────────────────────────────────────

def test_fee_budget_blocks_only_when_exceeded(monkeypatch):
    from backend.services.ledger import fee_budget as fb

    monkeypatch.setenv("FEE_BUDGET_DAILY_PCT", "0.3")
    db = MagicMock()
    # 权益 5000 → 预算 15；已付 14.5，本单名义 2000×3.5bp=0.7 → 超
    v = fb.check_fee_budget(db, 14, est_notional=2000, equity=5000, fees_today=14.5, fee_rate=0.00035)
    assert v.allowed is False and "fee_budget_exceeded" in v.reason
    # 已付 10 → 10.7 ≤ 15 放行
    v2 = fb.check_fee_budget(db, 14, est_notional=2000, equity=5000, fees_today=10.0, fee_rate=0.00035)
    assert v2.allowed is True
    # 关闭预算
    monkeypatch.setenv("FEE_BUDGET_DAILY_PCT", "0")
    v3 = fb.check_fee_budget(db, 14, est_notional=2000, equity=5000, fees_today=999, fee_rate=0.00035)
    assert v3.allowed is True and v3.reason == "fee_budget_disabled"


def test_fee_budget_live_daily_accumulator():
    from backend.services.ledger import fee_budget as fb

    fb._live_fees.clear()
    assert fb.fees_paid_today_live(188) == 0.0
    fb.record_live_fee(188, 0.4)
    fb.record_live_fee(188, 0.6)
    assert abs(fb.fees_paid_today_live(188) - 1.0) < 1e-9
    assert fb.fees_paid_today_live(189) == 0.0


def test_fee_budget_fail_open_on_db_error():
    from backend.services.ledger import fee_budget as fb

    class _BadDB:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

    fb.invalidate_cache()
    v = fb.check_fee_budget(_BadDB(), 999999, est_notional=100, equity=1000, source="paper")
    assert v.allowed is True and v.reason.startswith("fee_budget_error")


# ── 短线影子模式 ────────────────────────────────────────────────────────────

def test_scalp_shadow_default_on(monkeypatch):
    from backend.services.scalp.shadow_mode import scalp_shadow_enabled

    monkeypatch.delenv("SCALP_SHADOW_MODE", raising=False)
    assert scalp_shadow_enabled("paper") is True
    monkeypatch.setenv("SCALP_SHADOW_MODE", "false")
    assert scalp_shadow_enabled("paper") is False
    monkeypatch.setenv("SCALP_SHADOW_MODE", "1")
    assert scalp_shadow_enabled("live") is True


def test_scalp_shadow_record_uses_signal_log(monkeypatch):
    from backend.services.scalp import shadow_mode as sm
    import backend.services.scalp_signal_logger as ssl

    captured = {}

    def _fake_log_signal(**kw):
        captured.update(kw)

    monkeypatch.setattr(ssl, "log_signal", _fake_log_signal)
    ok = sm.record_shadow_fill(
        symbol="btc", direction="long", entry_price=100.0, tp_price=102.0, sl_price=99.0,
        leverage=3, notional_usd=250.0, factor_score=1.2, threshold=0.8,
        session_id="s", account_id=14, trade_mode="paper", features={"pwin": 0.6},
    )
    assert ok is True
    assert captured["action"] == "shadow_fill"
    assert abs(captured["tp_pct"] - 0.02) < 1e-9 and abs(captured["sl_pct"] - 0.01) < 1e-9
    assert captured["features"]["shadow"] is True and captured["features"]["would_notional_usd"] == 250.0


# ── tier 日亏预算口径守卫 ────────────────────────────────────────────────────

def test_tier_daily_pnl_sql_subtracts_fees():
    import inspect
    from backend.services import tier_circuit_breaker as tcb

    src = inspect.getsource(tcb.compute_tier_daily_pnl)
    assert "SUM(COALESCE(o.fee, 0))" in src, "tier 日亏预算必须扣手续费（v3 F1d）"
