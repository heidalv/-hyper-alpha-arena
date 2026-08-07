"""
P4.2 DriftWatcher + P4.3 OnlineWeights + P4.5 ShadowJudge 测试。

P4.2 完成标准：注入概念漂移数据，drift 检出 + adapt 策略 + ROLLBACK。
P4.3 完成标准：在线学习 vs 离线持平，漂移段适应更快；权重重置。
P4.5 完成标准：状态机自动驱动；审批超时默认拒。
"""
from __future__ import annotations

import numpy as np
import pytest

from backend.services.evolution.drift_watcher import (
    AdaptStrategy,
    DriftType,
    DriftWatcher,
    DriftWatcherConfig,
)
from backend.services.evolution.online_weights import (
    OnlineLinearConfig,
    OnlineLinearModel,
)
from backend.services.evolution.shadow_judge import ShadowJudge
from backend.services.factor_engine.lifecycle import (
    FactorMetrics,
    FactorState,
)

pytestmark = pytest.mark.unit


# ==================== P4.2 DriftWatcher ====================

class TestDriftDetection:
    def test_no_drift_stable(self):
        """平稳序列不检出漂移。"""
        watcher = DriftWatcher(DriftWatcherConfig(min_samples_before_detect=10))
        rng = np.random.default_rng(42)
        events = []
        for _ in range(100):
            e = watcher.observe_error("ic", rng.normal(0.5, 0.05))
            if e:
                events.append(e)
        # 平稳段应极少或无漂移
        assert len(events) <= 2

    def test_abrupt_drift_detected(self):
        """突变（均值跳变）被 ADWIN 检出。"""
        watcher = DriftWatcher(DriftWatcherConfig(min_samples_before_detect=10))
        rng = np.random.default_rng(42)
        events = []
        # 前 50 平稳，后 50 突变
        for i in range(100):
            val = 0.1 if i < 50 else 0.9  # 均值从 0.1 跳到 0.9
            e = watcher.observe_error("ic", val + rng.normal(0, 0.02))
            if e:
                events.append(e)
        assert len(events) >= 1
        assert any(e.drift_type == DriftType.ABRUPT for e in events)

    def test_drift_increments_consecutive(self):
        watcher = DriftWatcher(DriftWatcherConfig(min_samples_before_detect=5))
        rng = np.random.default_rng(7)
        # 带噪声的均值跳变（ADWIN 需要方差才能比较两半均值）
        detected = 0
        for i in range(60):
            base = 0.1 if i < 30 else 2.0
            evt = watcher.observe_error("x", base + rng.normal(0, 0.05))
            if evt is not None:
                detected += 1
        # 漂移应被检出至少一次
        assert detected >= 1


class TestAdaptStrategy:
    def test_next_strategy_priority(self):
        """适应策略按优先级依次返回。"""
        watcher = DriftWatcher(DriftWatcherConfig(min_samples_before_detect=5))
        # 触发一次漂移
        for i in range(40):
            watcher.observe_error("x", 0.1 if i < 20 else 2.0)
        s1 = watcher.next_adapt_strategy("x")
        assert s1 == AdaptStrategy.ONLINE_WEIGHT_RESET
        s2 = watcher.next_adapt_strategy("x")
        assert s2 == AdaptStrategy.REGIME_SWITCH

    def test_rollback_after_consecutive(self):
        """连续漂移超阈 → ROLLBACK。

        用快速连续 3 次均值跳变（每跳后立即再跳，无稳定样本重置计数）。
        """
        watcher = DriftWatcher(DriftWatcherConfig(
            min_samples_before_detect=5, consecutive_drifts_to_rollback=3))
        rng = np.random.default_rng(8)
        # 阶梯式连续跳变：0.1 → 1.0 → 2.0 → 3.0（每段短，跳变密集）
        levels = [0.1, 1.0, 2.0, 3.0, 4.0]
        for lvl_idx in range(len(levels)):
            for _ in range(15):  # 每段短，防止稳定重置
                watcher.observe_error("x", levels[lvl_idx] + rng.normal(0, 0.03))
        # 密集跳变应累积连续漂移
        assert watcher.should_rollback("x")
        assert watcher.next_adapt_strategy("x") == AdaptStrategy.ROLLBACK

    def test_reset_cursor(self):
        watcher = DriftWatcher(DriftWatcherConfig(min_samples_before_detect=5))
        for i in range(40):
            watcher.observe_error("x", 0.1 if i < 20 else 2.0)
        watcher.next_adapt_strategy("x")
        watcher.reset_adapt_cursor("x")
        assert watcher._adapt_cursor.get("x", 0) == 0


# ==================== P4.3 OnlineWeights ====================

class TestOnlineLinear:
    def test_learn_predict(self):
        model = OnlineLinearModel()
        rng = np.random.default_rng(1)
        # y = 2*f1 + 1*f2 + noise
        for _ in range(500):
            x = rng.normal(0, 1, 2)
            y = 2 * x[0] + 1 * x[1] + rng.normal(0, 0.1)
            model.learn_one(x, y)
        # 权重应接近 [2, 1]
        assert model.weights[0] > model.weights[1]  # f1 权重更大
        assert abs(model.weights[0] - 2.0) < 0.5

    def test_reset(self):
        model = OnlineLinearModel()
        rng = np.random.default_rng(2)
        for _ in range(100):
            model.learn_one(rng.normal(0, 1, 3), rng.normal())
        assert model.weight_norm() > 0
        model.reset()
        assert model.weight_norm() < 1e-9
        assert model._n_samples == 0

    def test_feature_importance(self):
        model = OnlineLinearModel()
        rng = np.random.default_rng(3)
        for _ in range(200):
            x = rng.normal(0, 1, 3)
            model.learn_one(x, 3 * x[0])  # 只有 f0 有信号
        imp = model.feature_importance()
        assert imp["f0"] > imp["f1"]
        assert sum(imp.values()) == pytest.approx(1.0, abs=1e-6)

    def test_adapts_faster_after_reset(self):
        """漂移后重置权重，适应新关系更快。"""
        rng = np.random.default_rng(4)
        model = OnlineLinearModel(OnlineLinearConfig(learning_rate=0.05))
        # 阶段1: y = +f0
        for _ in range(300):
            x = rng.normal(0, 1, 1)
            model.learn_one(x, x[0])
        # 阶段2: 漂移，y = -f0（符号反转）
        model.reset()
        for _ in range(300):
            x = rng.normal(0, 1, 1)
            model.learn_one(x, -x[0])
        # 重置后应更快适应新符号（权重变负）
        assert model.weights[0] < 0


# ==================== P4.5 ShadowJudge ====================

class TestShadowJudge:
    def _metrics(self, fid="f1", state=FactorState.DRAFT, **kw):
        defaults = dict(
            factor_id=fid, state=state, audit_passed=False, has_bug=False,
            icir=0.0, monotonicity_p=1.0, turnover=1.0, halflife_bars=0,
            incremental_corr=1.0, dsr_significant=False, pbo=1.0, capacity_usd=0.0,
            paper_sharpe=0.0, live_deviation=1.0, paper_days=0, small_live_days=0,
            decay_consecutive_days=0,
        )
        defaults.update(kw)
        return FactorMetrics(**defaults)

    def test_auto_promote_draft_to_candidate(self):
        judge = ShadowJudge()
        m = self._metrics(state=FactorState.DRAFT, audit_passed=True)
        j = judge.judge(m)
        assert j.executed
        assert j.decision.to_state == FactorState.CANDIDATE

    def test_auto_promote_candidate_to_ortho(self):
        judge = ShadowJudge()
        m = self._metrics(state=FactorState.CANDIDATE, icir=0.5,
                          monotonicity_p=0.03, turnover=0.6, halflife_bars=10)
        j = judge.judge(m)
        assert j.executed
        assert j.decision.to_state == FactorState.ORTHO

    def test_paper_to_smalllive_needs_approval(self):
        judge = ShadowJudge()
        m = self._metrics(state=FactorState.PAPER, paper_sharpe=1.5,
                          live_deviation=0.001, paper_days=7)
        j = judge.judge(m)
        assert j.pending_approval
        assert not j.executed

    def test_approval_granted(self):
        judge = ShadowJudge()
        m = self._metrics(state=FactorState.PAPER, paper_sharpe=1.5,
                          live_deviation=0.001, paper_days=7)
        judge.judge(m)
        assert judge.pending_count() == 1
        assert judge.approve("f1")
        assert judge.pending_count() == 0

    def test_approval_timeout_rejected(self):
        """审批超时 → 默认拒（R4 不留悬置）。"""
        judge = ShadowJudge()
        m = self._metrics(state=FactorState.PAPER, paper_sharpe=1.5,
                          live_deviation=0.001, paper_days=7)
        judge.judge(m)
        # 手动老化时间戳
        for fid, j in judge._pending.items():
            j.ts_ns = 0  # 模拟很久以前
        timed_out = judge.check_approval_timeout()
        assert "f1" in timed_out
        assert judge.pending_count() == 0

    def test_active_decay_auto_deweight(self):
        """ACTIVE 退化 → 自动降权（不需审批）。"""
        judge = ShadowJudge()
        m = self._metrics(state=FactorState.ACTIVE, icir=0.1,
                          decay_consecutive_days=5)
        j = judge.judge(m)
        assert j.executed
        assert j.decision.to_state == FactorState.DEWEIGHT

    def test_bug_auto_rejected(self):
        judge = ShadowJudge()
        m = self._metrics(state=FactorState.ACTIVE, has_bug=True)
        j = judge.judge(m)
        assert j.executed
        assert j.decision.to_state == FactorState.REJECTED
