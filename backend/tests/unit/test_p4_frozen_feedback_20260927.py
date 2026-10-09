# -*- coding: utf-8 -*-
"""[P4 大轮回 2026-09-27] §11.3 解耦契约：冻结（learning_enabled=false）≠ 停止全局回灌。"""
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from backend.services.unified_learning_service import UnifiedLearningService  # noqa: E402


class _FakeDB:
    def __init__(self):
        self.committed = 0

    def commit(self):
        self.committed += 1


def _mk_outcome():
    from backend.services.unified_learning_service import TradeOutcome
    return TradeOutcome(
        source="paper", strategy_id="s_frozen", symbol="BTC", side="long",
        tier="mid", trade_nature="swing", entry_price=100.0, exit_price=101.0,
        pnl=5.0, pnl_pct=0.01, duration_seconds=3600,
        regime_at_entry="ranging", regime_at_exit="ranging",
        confidence=0.6, position_size=1.0, opened_at=None,
        peak_pnl_pct=0.01, exit_pnl_pct=0.01, retention_ratio=1.0,
        health_at_exit=None, reversal_level_at_exit="", exit_channel="tp",
        metadata={"thesis_id": "t_test", "account_id": 14,
                  "paper_position_id": 1, "close_fee": 0.1},
    )


@pytest.fixture()
def _fakes(monkeypatch):
    calls = {"mlto": 0, "bus": 0, "review": 0, "midlong": 0, "regime": 0,
             "memory": 0}

    fake_bridge = types.ModuleType("backend.services.mlto.learning_bridge")
    fake_bridge.record_outcome = lambda adb, outcome, **kw: calls.__setitem__("mlto", calls["mlto"] + 1)
    monkeypatch.setitem(sys.modules, "backend.services.mlto.learning_bridge", fake_bridge)

    fake_bus_mod = types.ModuleType("backend.services.learning_bus")
    class _Bus:
        def enqueue_thesis_postmortem(self, outcome):
            calls["bus"] += 1
    fake_bus_mod.get_learning_bus = lambda: _Bus()
    monkeypatch.setitem(sys.modules, "backend.services.learning_bus", fake_bus_mod)

    fake_ro = types.ModuleType("backend.services.mlto.trade_review_officer")
    fake_ro.review_close = lambda db, outcome: calls.__setitem__("review", calls["review"] + 1)
    monkeypatch.setitem(sys.modules, "backend.services.mlto.trade_review_officer", fake_ro)

    fake_mcg = types.ModuleType("backend.services.full_auto.midlong_circuit_gate")
    fake_mcg.record_midlong_outcome = lambda *a, **k: calls.__setitem__("midlong", calls["midlong"] + 1)
    monkeypatch.setitem(sys.modules, "backend.services.full_auto.midlong_circuit_gate", fake_mcg)

    svc = UnifiedLearningService()
    monkeypatch.setattr(svc, "_is_learning_enabled", lambda sid: False)
    monkeypatch.setattr(svc, "_persist_strategy_trade", lambda db, o: True)
    monkeypatch.setattr(svc, "_update_regime_score",
                        lambda *a, **k: calls.__setitem__("regime", calls["regime"] + 1))
    monkeypatch.setattr(svc, "_update_strategy_memory",
                        lambda *a, **k: calls.__setitem__("memory", calls["memory"] + 1))
    monkeypatch.setattr(svc, "_track_loss_streak", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_check_adaptation_needed", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_check_divergence", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_check_prompt_evolution_trigger", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_evaluate_wisdom_effectiveness", lambda *a, **k: None)
    # AnalyticsSessionLocal（MLTO 块内用）：换成假连接
    fake_conn_mod = types.ModuleType("backend.database.connection")
    from backend.database.connection import SessionLocal as _real_sl
    class _FakeAnalytics:
        def __init__(self, *a, **k):
            self._closed = False
        def close(self):
            self._closed = True
    fake_conn_mod.AnalyticsSessionLocal = _FakeAnalytics
    fake_conn_mod.SessionLocal = _real_sl
    monkeypatch.setitem(sys.modules, "backend.database.connection", fake_conn_mod)
    return calls


def test_frozen_strategy_still_gets_global_feedback(_fakes):
    db = _FakeDB()
    svc = UnifiedLearningService()
    from backend.services.unified_learning_service import TradeOutcome
    svc.process_outcome(db, _mk_outcome())
    assert _fakes["mlto"] == 1, "冻结策略的平仓结果必须仍回灌论题学习"
    assert _fakes["bus"] == 1, "postmortem 必须照常入队"
    assert _fakes["review"] == 1, "复盘官必须照常运行"
    assert _fakes["midlong"] == 1, "熔断记账必须照常（账户级风险状态）"
    assert db.committed >= 1


def test_frozen_strategy_skips_strategy_param_updates(_fakes):
    db = _FakeDB()
    svc = UnifiedLearningService()
    svc.process_outcome(db, _mk_outcome())
    assert _fakes["regime"] == 0, "冻结必须跳过策略自身绩效矩阵更新"
    assert _fakes["memory"] == 0, "冻结必须跳过策略记忆更新"


def test_global_feedback_account_id_extraction():
    from backend.services.unified_learning_service import _outcome_account_id
    o = _mk_outcome()
    assert _outcome_account_id(o) == 14
    o.metadata = {"account_id": None}
    assert _outcome_account_id(o) is None
