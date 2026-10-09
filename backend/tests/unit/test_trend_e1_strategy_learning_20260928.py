# -*- coding: utf-8 -*-
"""[2026-09-28 用户指令·补漏] 长线车道 `trend_e1:<SYMBOL>` 的平仓学习被静默跳过——回归锁。

## 现场（09-28 实测，账户 14）
- SOL #4815（trend_e1:SOL，breakeven_tp，+2.86）与 AVAX #4801（trend_e1:AVAX，breakeven_tp，+3.87）
  两笔长线平仓：postmortem=0、owm_bump=0，且日志**没有任何失败告警**。
- 根因链：`_persist_strategy_trade` → `_resolve_strategy_id_for_fk("trend_e1:SOL")` 返回 None
  （该 id 不在 ai_strategies 表，也不在系统策略前缀表里）⇒ 返回 False ⇒
  `process_outcome` 在**MLTO 学习块之前**按"重复 outcome"静默 return（DEBUG 级日志）。
- 即：长线车道的平仓此前**整条学习链都不进**——比 thesis_id 缺失更深一层。

## 本文件锁什么
① `trend_e1` 已进系统策略前缀表 ⇒ FK 解析会为其建占位父行、返回有效 id；
② 同一 id 重复调用幂等（不重复建行）；
③ 修复后，长线形态的 outcome 能一路走到 MLTO 块（mlto_record 被调用）。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import backend.services.unified_learning_service as U


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MLTO_LEARNING_THESIS_FALLBACK", raising=False)
    yield


def _mk_db(first_strategy_row=None):
    """AIStrategy 查询链与 Account 查询链分离的假 db。

    注意：代码第一个查询是 `.filter(...).first()`（**无** `.order_by`），
    若只给 `filter.return_value.order_by.return_value.first` 赋值，
    MagicMock 会在 `filter.return_value.first` 处自动造出真值 ⇒ 假"行已存在"。
    """

    class _DB:
        def __init__(self):
            self.added = None
            self.add_calls = 0

        def query(self, model, *a, **k):
            name = str(model)
            q = MagicMock()
            if "Account" in name or "accounts" in name:
                q.order_by.return_value.first.return_value = (7,)
                return q
            # AIStrategy 相关：两级 first 都要给（带/不带 order_by 两种链）
            q.filter.return_value.first.return_value = first_strategy_row
            q.filter.return_value.order_by.return_value.first.return_value = first_strategy_row
            return q

        def add(self, obj):
            self.added = obj
            self.add_calls += 1

        def flush(self):
            pass

        def rollback(self):
            pass

    return _DB()


def test_fk_resolver_creates_placeholder_for_trend_e1(monkeypatch):
    """修复核心：trend_e1:<SYMBOL> 必须能解析出有效 strategy_id（建占位行）。"""
    db = _mk_db(first_strategy_row=None)
    svc = U.UnifiedLearningService()
    sid = svc._resolve_strategy_id_for_fk(db, "trend_e1:SOL")
    assert sid == "trend_e1:SOL", f"FK 解析失败: {sid}"
    assert db.added is not None, "必须为系统策略创建占位父行"
    assert db.add_calls == 1


def test_resolver_returns_existing_row_without_creating(monkeypatch):
    """幂等：已有行时不重复建占位。"""
    db = _mk_db(first_strategy_row=("trend_e1:SOL",))
    svc = U.UnifiedLearningService()
    assert svc._resolve_strategy_id_for_fk(db, "trend_e1:SOL") == "trend_e1:SOL"
    assert db.add_calls == 0, "已有行不得再建占位"


def test_long_lane_outcome_reaches_mlto_block(monkeypatch):
    """端到端：trend_e1 形态的平仓在修复后必须走到 MLTO 块（mlto_record 被调用一次）。"""
    import backend.services.mlto.learning_bridge as LBR
    from backend.services.mlto import thesis_store as TS

    calls = {"record": 0}
    monkeypatch.setattr(LBR, "record_outcome",
                        lambda db, outcome, analytics_db=None: calls.__setitem__("record", calls["record"] + 1))
    monkeypatch.setattr(TS, "find_latest", lambda sym, tier, **k: SimpleNamespace(
        thesis_id="25e9c215-6a81-47dc-b275-381825a23682", session_id="fa_7e12e7a1b6"))
    monkeypatch.setattr(TS, "get", lambda *a, **k: None)

    class _Bus:
        def enqueue_thesis_postmortem(self, outcome):
            calls["bus"] = calls.get("bus", 0) + 1
            return True

    import backend.services.learning_bus as LB
    monkeypatch.setattr(LB, "get_learning_bus", lambda: _Bus())

    svc = U.UnifiedLearningService()
    for name in ("_update_regime_score", "_update_strategy_memory", "_track_loss_streak",
                 "_check_adaptation_needed", "_check_divergence",
                 "_check_prompt_evolution_trigger", "_evaluate_wisdom_effectiveness"):
        monkeypatch.setattr(svc, name, lambda *a, **k: None, raising=False)
    monkeypatch.setattr(svc, "_is_learning_enabled", lambda *a, **k: True, raising=False)
    # _persist_strategy_trade 用真实实现：需要能解析 trend_e1 → 建占位行 → 返回 True
    class _DB:
        def query(self, model, *a, **k):
            name = str(model)
            q = MagicMock()
            if "Account" in name or "accounts" in name:
                q.order_by.return_value.first.return_value = (7,)
                return q
            q.filter.return_value.first.return_value = None
            q.filter.return_value.order_by.return_value.first.return_value = None
            return q

        def add(self, obj):
            pass

        def flush(self):
            pass

        def commit(self):
            pass

        def rollback(self):
            pass

    out = U.TradeOutcome(
        source="paper", strategy_id="trend_e1:SOL", symbol="SOL", side="long",
        tier="trend_follow", entry_price=117.62, exit_price=119.96, pnl=2.86,
        pnl_pct=0.02, duration_seconds=20000,
        metadata={"timeframe_tier": "long", "close_reason": "breakeven_tp",
                  "paper_position_id": 4815},
    )
    svc.process_outcome(_DB(), out)
    assert calls["record"] == 1, f"MLTO 块未进入（record={calls['record']}）——trend_e1 平仓仍被静默跳过"
    assert calls.get("bus") == 1
