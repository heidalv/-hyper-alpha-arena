"""proposal_validation_policy 单元测试。"""
import pytest
from unittest.mock import patch

from backend.services.proposal_validation_policy import (
    PACE_VALIDATION_POLICY,
    can_evaluate_proposal,
    should_mark_inconclusive,
    validation_policy_for_gear,
)


@pytest.fixture(autouse=True)
def _disable_training_phase():
    """本组用例校验的是"纯 Pace 档位策略"契约。生产环境 training_phase 激活时
    会走 TRAINING_NARROW_POLICY（max_wait=24）覆盖档位值，导致 turbo(6h) 被顶成
    24h。单测里强制关闭 training_phase，隔离该 live 状态，使断言确定性。
    生产覆盖行为不受影响。"""
    with patch("backend.services.training_phase_service.is_active", return_value=False):
        yield


def test_turbo_policy_hours():
    pol = validation_policy_for_gear("turbo")
    assert pol["min_age_hours"] == PACE_VALIDATION_POLICY["turbo"]["min_age_hours"]
    assert pol["max_wait_hours"] == PACE_VALIDATION_POLICY["turbo"]["max_wait_hours"]
    assert pol["mode"] == "post_apply_slice"


def test_can_evaluate_when_samples_and_age_ok():
    ok, reason = can_evaluate_proposal(age_hours=3, post_apply_closed=6, gear="turbo")
    assert ok is True
    assert reason == "ready"


def test_defer_when_samples_low():
    ok, reason = can_evaluate_proposal(age_hours=10, post_apply_closed=2, gear="turbo")
    assert ok is False
    assert "samples" in reason


def test_defer_when_age_too_short():
    ok, reason = can_evaluate_proposal(age_hours=1, post_apply_closed=8, gear="turbo")
    assert ok is False
    assert "age" in reason


def test_inconclusive_after_long_wait():
    assert should_mark_inconclusive(age_hours=20, post_apply_closed=1, gear="turbo") is True
    assert should_mark_inconclusive(age_hours=5, post_apply_closed=1, gear="turbo") is False
