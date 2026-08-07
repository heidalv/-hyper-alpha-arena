"""
编排器软降级 + AI 优先 回归测试

改动 1：DIRECTION_COHERENCE_MODE 默认 audit —— DCP 本该 BLOCK 的方向放行+记日志
改动 4：nature 仲裁 —— AI trade_nature 优先，编排器建议仅标注不覆盖

本测试覆盖：
1. DCP audit 模式下，AI 方向与编排器对立时仍放行（allowed=True, audit_only=True）。
2. DCP enforce 模式下，同样场景会 BLOCK（对照，证明 audit 是有效开关）。
3. 配置默认值：DIRECTION_COHERENCE_MODE/STRICT_DATA_GATE/ORCHESTRATOR_HARD_GATE 的新默认。
"""
import os
import pytest
from unittest.mock import patch


pytestmark = pytest.mark.unit


def _orch_bearish(conf=80):
    """构造一个看空(与做多AI对立)的编排器决策字典。"""
    return {
        "final_side": "short",
        "weighted_confidence": conf,
        "long_bias": "bearish",
        "long_conf": conf,
        "mid_bias": "bearish",
        "mid_conf": conf,
        "short_bias": "bearish",
        "short_conf": conf,
    }


def test_dcp_audit_mode_allows_opposing_direction():
    """audit 模式：AI 做多 vs 编排器看空，应放行(audit_only=True)而非拦截。"""
    from backend.services.decision_core.direction_coherence import evaluate_direction_coherence

    # _dcp_mode() 内部 from backend.config.settings import DIRECTION_COHERENCE_MODE，
    # 所以 patch settings 模块属性（不是 direction_coherence 模块）。
    with patch("backend.config.settings.DIRECTION_COHERENCE_MODE", "audit"):
        verdict = evaluate_direction_coherence(
            action="buy",  # AI 做多
            confidence=60,
            tier="mid",
            trade_nature="swing",
            orchestrator=_orch_bearish(conf=80),  # 编排器强看空
            symbol="BTC",
        )
    # audit 模式：允许开仓（不拦截 AI）
    assert verdict.allowed is True
    # 但标记为 audit_only（记录了本会被拦截）
    assert verdict.audit_only is True


def test_dcp_enforce_mode_blocks_opposing_direction():
    """enforce 模式：AI 做多 vs 编排器强看空，应 BLOCK（对照证明开关有效）。"""
    from backend.services.decision_core.direction_coherence import evaluate_direction_coherence

    with patch("backend.config.settings.DIRECTION_COHERENCE_MODE", "enforce"):
        verdict = evaluate_direction_coherence(
            action="buy",
            confidence=60,
            tier="mid",
            trade_nature="swing",
            orchestrator=_orch_bearish(conf=80),
            symbol="BTC",
        )
    # enforce 模式：拦截
    assert verdict.allowed is False


def test_dcp_allows_aligned_direction_in_both_modes():
    """方向一致(AI做多+编排器看多)：两种模式都应放行。"""
    from backend.services.decision_core.direction_coherence import evaluate_direction_coherence

    orch_bullish = {k: ("bullish" if "bias" in k else 80) for k in _orch_bearish()}
    orch_bullish["final_side"] = "long"
    for mode in ("enforce", "audit"):
        with patch("backend.config.settings.DIRECTION_COHERENCE_MODE", mode):
            verdict = evaluate_direction_coherence(
                action="buy",
                confidence=60,
                tier="mid",
                trade_nature="swing",
                orchestrator=orch_bullish,
                symbol="ETH",
            )
        assert verdict.allowed is True, f"mode={mode} 方向一致应放行"


def test_new_config_defaults():
    """验证软降级后的三个配置默认值（显式清空 env，避免被其他测试的 env 污染）。"""
    import importlib

    import backend.config.settings as s

    # test_direction_coherence.py 在 import 时 setdefault("enforce") 会污染 env。
    # 这里显式删掉这些 env 变量再 reload，确保读到的是 settings.py 的新默认值。
    saved = {}
    for k in ("DIRECTION_COHERENCE_MODE", "ORCHESTRATOR_HARD_GATE", "STRICT_DATA_GATE"):
        if k in os.environ:
            saved[k] = os.environ.pop(k)
    try:
        importlib.reload(s)
        assert s.DIRECTION_COHERENCE_MODE == "audit", "DCP 默认应为 audit（AI 方向优先）"
        assert s.STRICT_DATA_GATE is True, "STRICT_DATA_GATE 默认应开启"
        assert s.ORCHESTRATOR_HARD_GATE is False, "硬门控默认应关闭"
    finally:
        # 恢复 env（避免影响后续测试）
        for k, v in saved.items():
            os.environ[k] = v
        importlib.reload(s)
