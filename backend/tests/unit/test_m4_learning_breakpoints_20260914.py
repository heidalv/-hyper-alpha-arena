# -*- coding: utf-8 -*-
"""[M4 2026-09-14] 学习链断点修复契约测试。

背景（审计实证）：
- maturity_state.json 自 2026-08-16 冻结（run_maturity_tick 无定时调用者），
  6 处门控消费 28 天前快照。
- QAA 5min 优化周期空转：feed_outcome 不传 decision_quality → 评分恒 0.5 →
  trend 恒 stable → 优化计划恒 0（24,367 条历史 optimize_*=0）。
- 灰度发布唯一入口 queue_optimization 在 paper 下被降级短路 → 0 灰度记录。

契约：
- 成熟度定时任务注册参数：task_id=maturity_tick、每 6h、func=maturity_controller.run_maturity_tick。
- outcome_decision_quality：盈利高/亏损低/0 中性/边界截断/非法输入回退 0.5。
- paper（loss_locks_disabled）下 _heal_pause_reoptimize：排队重优化 + 只降风险不暂停。
"""
import time
from types import SimpleNamespace

import pytest

import backend.services.strategy_health_service as shs
from backend.services.qaa_evolution_bridge import outcome_decision_quality


class _FakeScheduler:
    def __init__(self):
        self.calls = []

    def add_interval_task(self, **kwargs):
        self.calls.append(kwargs)


class _FakeDb:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def rollback(self):
        pass


# ── 1. 成熟度定时注册 ──

def test_maturity_tick_registered_with_expected_params():
    from backend.services.startup import register_maturity_tick
    from backend.services.maturity_controller import run_maturity_tick

    sched = _FakeScheduler()
    register_maturity_tick(sched)
    assert len(sched.calls) == 1
    call = sched.calls[0]
    assert call["task_id"] == "maturity_tick"
    assert call["interval_seconds"] == 6 * 3600
    assert call["task_func"] is run_maturity_tick


# ── 2. QAA 决策质量折算 ──

def test_qaa_quality_win_high_loss_low():
    assert outcome_decision_quality(0.06) == pytest.approx(0.98)
    assert outcome_decision_quality(-0.06) == pytest.approx(0.02)
    assert outcome_decision_quality(0.0) == 0.5


def test_qaa_quality_clips_to_bounds():
    assert outcome_decision_quality(0.20) == 1.0
    assert outcome_decision_quality(-0.20) == 0.0


def test_qaa_quality_invalid_falls_back_neutral():
    assert outcome_decision_quality(None) == 0.5
    assert outcome_decision_quality("abc") == 0.5


def test_qaa_quality_old_behavior_distinguishable():
    """旧行为恒 0.5 无法区分胜负；新口径下盈利/亏损评分必须不同（退化检测前提）。"""
    assert outcome_decision_quality(0.03) > 0.5 > outcome_decision_quality(-0.03)


# ── 3. paper 灰度双轨 ──

def test_paper_heal_queues_reoptimize_and_keeps_trading(monkeypatch):
    import backend.services.risk_management.loss_lock_policy as llp
    import backend.services.auto_optimizer as ao

    monkeypatch.setattr(llp, "loss_locks_disabled", lambda: True, raising=False)
    queued = []
    monkeypatch.setattr(
        ao.AutoOptimizer, "queue_optimization",
        lambda self, sid: queued.append(sid), raising=False,
    )

    strategy = SimpleNamespace(
        strategy_id="tpl_mid_range_444876",
        status="running",
        primary_symbol="UNI",
        risk_params={"risk_pct": 2.0},
    )
    svc = shs.StrategyHealthService()
    result = svc._heal_pause_reoptimize(strategy, _FakeDb())

    assert queued == ["tpl_mid_range_444876"]          # 重优化已排队（灰度入口打通）
    assert result["action"] == shs.HealAction.REDUCE_RISK.value  # 只降风险
    assert strategy.status == "running"                  # 绝不暂停（纸面=训练数据）
    assert strategy.risk_params["risk_pct"] < 2.0        # 缩仓生效


def test_live_heal_still_pauses_and_reoptimizes(monkeypatch):
    """live 口径：行为不变（暂停 + 重优化）。"""
    import backend.services.risk_management.loss_lock_policy as llp
    import backend.services.auto_optimizer as ao

    monkeypatch.setattr(llp, "loss_locks_disabled", lambda: False, raising=False)
    queued = []
    monkeypatch.setattr(
        ao.AutoOptimizer, "queue_optimization",
        lambda self, sid: queued.append(sid), raising=False,
    )
    monkeypatch.setattr(
        shs.symbol_lock_registry if hasattr(shs, "symbol_lock_registry") else shs,
        "__unused__", None, raising=False,
    )

    strategy = SimpleNamespace(
        strategy_id="tpl_long_swing_42c24a",
        status="running",
        primary_symbol="SOL",
        risk_params={"risk_pct": 2.0},
    )
    svc = shs.StrategyHealthService()
    result = svc._heal_pause_reoptimize(strategy, _FakeDb())
    assert strategy.status == "paused"
    assert result["action"] == shs.HealAction.PAUSE_AND_REOPTIMIZE.value
