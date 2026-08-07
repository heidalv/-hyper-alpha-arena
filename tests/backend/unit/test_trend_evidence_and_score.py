"""TrendAgent 证据清单与纸盘开仓分门槛。"""

from backend.services.agent_evidence_builder import build_trend_evidence
from backend.config.settings import get_trend_min_score_to_open
from backend.services.trend_agent import resolve_trend_min_score


def test_build_trend_evidence_includes_4h_1w_resonance():
    envs = {
        "BTC": {
            "indicators_4h": {"ema_trend": "bullish", "rsi": 58},
            "indicators_1d": {"ema_trend": "bullish", "rsi": 52},
            "indicators_1w": {"ema_trend": "bullish"},
            "orchestrator": {"mid_bias": "bullish", "mid_confidence": 0.6},
        }
    }
    facts = {f.id: f for f in build_trend_evidence("BTC", envs)}
    assert facts["trend_4h"].value == "bullish"
    assert facts["trend_1d"].value == "bullish"
    assert facts["trend_1w"].value == "bullish"
    assert facts["trend_4h_1d_resonance"].value == "共振_bullish"


def test_paper_trend_min_score_default_40():
    assert get_trend_min_score_to_open("paper") == 40
    assert resolve_trend_min_score("paper") == 40


def test_live_trend_min_score_default_50():
    assert get_trend_min_score_to_open("live") == 50
    assert resolve_trend_min_score("live") == 50
