import pytest


@pytest.mark.unit
class TestTrendHealthScore:
    def test_strong_trend_scores_high_when_aligned(self):
        from backend.services.trend_health_score import get_trend_health_scorer

        result = get_trend_health_scorer().evaluate(
            symbol="SOL",
            side="long",
            trade_nature="trend_follow",
            market_env={
                "price": 160,
                "orchestrator": {
                    "long_bias": "bullish",
                    "mid_bias": "bullish",
                    "short_bias": "bullish",
                    "long_conf": 80,
                    "mid_conf": 75,
                    "short_conf": 70,
                },
                "indicators": {
                    "adx_4h": 32,
                    "ema_slope_4h": 0.01,
                    "macd_hist": 1.2,
                    "macd_hist_prev": 0.8,
                    "swing_low": 145,
                },
            },
        )

        assert result.score >= 70
        assert result.regime == "strong_trend"
        assert result.aligned_with_position is True

    def test_opposite_long_bias_caps_score(self):
        from backend.services.trend_health_score import get_trend_health_scorer

        result = get_trend_health_scorer().evaluate(
            symbol="SOL",
            side="long",
            trade_nature="trend_follow",
            market_env={
                "price": 140,
                "orchestrator": {
                    "long_bias": "bearish",
                    "mid_bias": "bearish",
                    "short_bias": "bearish",
                    "long_conf": 80,
                    "mid_conf": 75,
                    "short_conf": 70,
                },
                "indicators": {
                    "adx_4h": 30,
                    "ema_slope_4h": -0.01,
                    "macd_hist": -0.5,
                    "macd_hist_prev": 0.4,
                    "swing_low": 145,
                },
            },
        )

        assert result.score <= 45
        assert result.aligned_with_position is False
        assert result.regime in ("reversal_risk", "broken")
