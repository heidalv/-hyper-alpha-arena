"""
TrendAgent 单元测试

验证：
1. 趋势方向分析（强趋势→long、弱趋势→veto）
2. 持仓复查（浮盈+趋势在→hold、趋势反转→close）
3. 补仓判断（浮亏→skip、浮盈→可add）
4. LLM 回退（规则回退路径）
5. 90min 节流常量
6. is_trend_nature 判定
"""
import pytest
from unittest.mock import patch, MagicMock


pytestmark = pytest.mark.unit


def _benign_crypto_bundle():
    """构造一个"无尾部风险"的 crypto_alpha bundle，用于把 _normalize_direction
    从 live 网络数据（清算簇/funding 背离）中隔离出来，专注测试归一化契约本身。
    生产环境仍走真实数据，仅单测隔离。"""
    bundle = MagicMock()
    bundle.liquidation_magnet.available = False
    bundle.liquidation_magnet.severity = "low"
    bundle.liquidation_magnet.direction = "neutral"
    bundle.funding_oi_divergence.available = False
    bundle.funding_oi_divergence.strength = 0.0
    bundle.funding_oi_divergence.direction = "neutral"
    return bundle


def test_is_trend_nature():
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    assert ta.is_trend_nature("trend_follow") is True
    assert ta.is_trend_nature("position") is True
    assert ta.is_trend_nature("scalp") is False
    assert ta.is_trend_nature("swing") is False
    assert ta.is_trend_nature("") is False


def test_normalize_direction_strong_trend():
    """趋势评分高 → should_open=True。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    result = {"trend_score": 85, "trend_direction": "long",
              "should_open_trend": True, "suggested_sl_pct": 0.08, "reasoning": "多周期共振"}
    with patch("backend.services.crypto_alpha_signals.crypto_alpha.get_bundle",
               return_value=_benign_crypto_bundle()):
        norm = ta._normalize_direction(result, "BTC", "buy")
    assert norm["score"] == 85
    assert norm["should_open"] is True
    assert norm["direction"] == "long"


def test_normalize_direction_weak_trend_vetoed():
    """趋势评分低于阈值 → should_open=False（veto 开仓）。"""
    from backend.services.trend_agent import TrendAgent, TREND_MIN_SCORE_TO_OPEN

    ta = TrendAgent()
    result = {"trend_score": 30, "trend_direction": "neutral",
              "should_open_trend": True, "suggested_sl_pct": 0.05}
    norm = ta._normalize_direction(result, "BTC", "buy")
    assert norm["score"] == 30
    # 即使 LLM 说 should_open=True，score < 阈值仍 veto
    assert norm["should_open"] is False
    assert norm["score"] < TREND_MIN_SCORE_TO_OPEN


def test_normalize_direction_sl_floor():
    """建议 SL 不能太小（趋势仓至少 4%）。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    result = {"trend_score": 70, "trend_direction": "long",
              "should_open_trend": True, "suggested_sl_pct": 0.01}  # 1% 太紧
    norm = ta._normalize_direction(result, "BTC", "buy")
    assert norm["suggested_sl_pct"] >= 0.04


def test_normalize_direction_returns_scenario_fields():
    """方向分析应返回结构化 scenario 字段供落库。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    result = {
        "trend_score": 80,
        "trend_direction": "long",
        "should_open_trend": True,
        "suggested_sl_pct": 0.08,
        "lifecycle": "加速",
        "scenario_a": "突破前高",
        "scenario_b": "震荡",
        "scenario_c": "闪崩",
        "reasoning": "多周期共振",
    }
    norm = ta._normalize_direction(result, "BTC", "long")
    assert norm["scenario_a"] == "突破前高"
    assert norm["scenario_b"] == "震荡"
    assert norm["lifecycle"] == "加速"


def test_fallback_direction_uses_long_confidence():
    """编排器 long_confidence 字段应被 fallback 正确读取。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    market_envs = {"BTC": {"orchestrator": {"long_bias": "bullish", "long_confidence": 0.75}}}
    result = ta._fallback_direction("BTC", "buy", market_envs)
    assert result["score"] >= 45
    assert result["direction"] == "long"


def test_fallback_direction_conservative():
    """LLM 失败时规则回退：保守（编排器弱信号给低分）。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    market_envs = {"BTC": {"orchestrator": {"long_bias": "neutral", "long_conf": 0.1}}}
    result = ta._fallback_direction("BTC", "buy", market_envs)
    assert result["score"] < 50  # neutral 给低分
    assert result["should_open"] is False


def test_normalize_review_hold():
    """持仓复查：趋势在 → hold。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    result = {"action": "hold", "trend_still_valid": True, "reasoning": "趋势仍在"}
    norm = ta._normalize_review(result, "BTC")
    assert norm["action"] == "hold"


def test_normalize_review_reduce_ratio_clamped():
    """减仓比例在安全范围 [0.1, 0.8]。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    result = {"action": "reduce", "reduce_ratio": 0.95}  # 超上限
    norm = ta._normalize_review(result, "BTC")
    assert norm["action"] == "reduce"
    assert 0.1 <= norm["reduce_ratio"] <= 0.8


def test_normalize_review_trend_adjustment():
    """止盈止损优化：trailing/staged 写入 trend_adjustment。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    result = {"action": "tighten_trailing", "trailing_atr_mult": 1.5,
              "staged_tp_adjust": "raise"}
    norm = ta._normalize_review(result, "BTC")
    assert norm["action"] == "tighten_trailing"
    assert norm["trend_adjustment"]["trailing_atr_mult"] == 1.5
    assert norm["trend_adjustment"]["staged_tp_adjust"] == "raise"


def test_fallback_review_big_loss_closes():
    """LLM 失败 + 浮亏超 6% → 规则回退建议 close。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    result = ta._fallback_review("BTC", {"pnl_pct": -8})
    assert result["action"] == "close"


def test_fallback_review_small_pnl_holds():
    """LLM 失败 + 浮亏不大 → 保守 hold。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    result = ta._fallback_review("BTC", {"pnl_pct": 2})
    assert result["action"] == "hold"


def test_review_interval_constant():
    """90 分钟节流常量正确。"""
    from backend.services.trend_agent import TREND_REVIEW_INTERVAL_SEC
    assert TREND_REVIEW_INTERVAL_SEC == 5400  # 90min


def test_max_per_tick_constant():
    """每 tick 最多复查 2 个趋势持仓。"""
    from backend.services.trend_agent import TREND_REVIEW_MAX_PER_TICK
    assert TREND_REVIEW_MAX_PER_TICK == 2


def test_llm_failure_returns_none():
    """LLM 调用失败时返回 None（触发回退）。"""
    from backend.services.trend_agent import TrendAgent

    ta = TrendAgent()
    with patch("backend.services.llm_config_service.get_llm_config_for_analysis", return_value=None):
        result = ta._call_llm("test", None, "TrendAgent:test")
    assert result is None
