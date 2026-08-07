"""
P1.3 因子生命周期状态机测试。

完成标准（方案 P1.3）：全状态转换单测覆盖；阈值边界正确；
审批门逻辑（高破坏性转换需审批，超时默认拒的精神由调用方实现）。
"""
from __future__ import annotations

import pytest

from backend.services.factor_engine.lifecycle import (
    APPROVAL_REQUIRED,
    FactorMetrics,
    FactorState,
    LifecycleThresholds,
    evaluate_transition,
    load_thresholds,
    needs_approval,
)

pytestmark = pytest.mark.unit


def _metrics(state=FactorState.DRAFT, **kw) -> FactorMetrics:
    """构造因子指标，默认全不达标，用 kw 覆盖。"""
    defaults = dict(
        factor_id="f1", state=state, audit_passed=False, has_bug=False,
        icir=0.0, monotonicity_p=1.0, turnover=1.0, halflife_bars=0,
        incremental_corr=1.0, dsr_significant=False, pbo=1.0, capacity_usd=0.0,
        paper_sharpe=0.0, live_deviation=1.0, paper_days=0, small_live_days=0,
        decay_consecutive_days=0,
    )
    defaults.update(kw)
    return FactorMetrics(**defaults)


class TestDraftToCandidate:
    def test_audit_pass_promotes(self):
        m = _metrics(FactorState.DRAFT, audit_passed=True)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.CANDIDATE
        assert d.auto

    def test_audit_fail_stays(self):
        m = _metrics(FactorState.DRAFT, audit_passed=False)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.DRAFT
        assert not d.auto


class TestCandidateToOrtho:
    def test_all_met_promotes(self):
        m = _metrics(FactorState.CANDIDATE, icir=0.5, monotonicity_p=0.03,
                     turnover=0.6, halflife_bars=10)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.ORTHO
        assert d.auto

    def test_low_icir_blocked(self):
        m = _metrics(FactorState.CANDIDATE, icir=0.1, monotonicity_p=0.03,
                     turnover=0.6, halflife_bars=10)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.CANDIDATE
        assert "icir" in d.conditions_failed

    def test_high_turnover_blocked(self):
        m = _metrics(FactorState.CANDIDATE, icir=0.5, monotonicity_p=0.03,
                     turnover=0.85, halflife_bars=10)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.CANDIDATE


class TestOrthoToPaper:
    def test_all_met_promotes_auto(self):
        m = _metrics(FactorState.ORTHO, incremental_corr=0.3, dsr_significant=True,
                     pbo=0.3, capacity_usd=2e5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.PAPER
        assert d.auto
        assert not needs_approval(d)  # PAPER 是影子，非破坏性，不需审批

    def test_high_corr_blocked(self):
        m = _metrics(FactorState.ORTHO, incremental_corr=0.7, dsr_significant=True,
                     pbo=0.3, capacity_usd=2e5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.ORTHO

    def test_high_pbo_blocked(self):
        m = _metrics(FactorState.ORTHO, incremental_corr=0.3, dsr_significant=True,
                     pbo=0.7, capacity_usd=2e5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.ORTHO
        assert "pbo" in d.conditions_failed


class TestPaperToSmallLive:
    def test_promotes_with_approval(self):
        m = _metrics(FactorState.PAPER, paper_sharpe=1.5, live_deviation=0.001,
                     paper_days=7)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.SMALL_LIVE
        assert needs_approval(d)

    def test_insufficient_paper_days(self):
        m = _metrics(FactorState.PAPER, paper_sharpe=1.5, live_deviation=0.001,
                     paper_days=2)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.PAPER


class TestSmallLiveToActive:
    def test_promotes_with_approval(self):
        m = _metrics(FactorState.SMALL_LIVE, small_live_days=20, icir=0.5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.ACTIVE
        assert needs_approval(d)

    def test_decay_during_smalllive_deweights(self):
        m = _metrics(FactorState.SMALL_LIVE, small_live_days=5,
                     icir=0.1, decay_consecutive_days=5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.DEWEIGHT
        assert d.auto
        assert not needs_approval(d)  # 自动降权不需审批


class TestActiveDecay:
    def test_icir_decay_deweights(self):
        m = _metrics(FactorState.ACTIVE, icir=0.1, decay_consecutive_days=5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.DEWEIGHT
        assert d.auto

    def test_capacity_decay_deweights(self):
        m = _metrics(FactorState.ACTIVE, icir=0.5, capacity_usd=1e4)  # 容量跌破
        d = evaluate_transition(m)
        assert d.to_state == FactorState.DEWEIGHT

    def test_healthy_active_stays(self):
        m = _metrics(FactorState.ACTIVE, icir=0.5, halflife_bars=20, capacity_usd=5e5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.ACTIVE


class TestDewightQuarantine:
    def test_persistent_decay_quarantines(self):
        m = _metrics(FactorState.DEWEIGHT, icir=0.1, decay_consecutive_days=5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.QUARANTINE
        assert d.auto

    def test_recovery_to_active(self):
        m = _metrics(FactorState.DEWEIGHT, icir=0.5, halflife_bars=20, capacity_usd=5e5)
        d = evaluate_transition(m)
        assert d.to_state == FactorState.ACTIVE


class TestBugRejection:
    def test_bug_rejected_from_any_state(self):
        for state in [FactorState.DRAFT, FactorState.ACTIVE, FactorState.PAPER]:
            m = _metrics(state, has_bug=True)
            d = evaluate_transition(m)
            assert d.to_state == FactorState.REJECTED
            assert d.auto


class TestThresholdsConfig:
    def test_default_thresholds(self):
        t = LifecycleThresholds()
        assert t.min_icir == 0.30
        assert t.max_pbo == 0.50

    def test_load_missing_yaml_returns_default(self, tmp_path):
        t = load_thresholds(tmp_path / "nonexistent.yaml")
        assert t.min_icir == 0.30

    def test_approval_set(self):
        assert FactorState.SMALL_LIVE in APPROVAL_REQUIRED
        assert FactorState.ACTIVE in APPROVAL_REQUIRED
        assert FactorState.ORTHO not in APPROVAL_REQUIRED
