import pytest


@pytest.mark.unit
class TestReversalSignalPack:
    def test_short_flip_with_long_intact_is_pullback(self):
        from backend.services.reversal_signal_pack import get_reversal_signal_builder
        from backend.services.trend_health_score import TrendHealthResult

        result = get_reversal_signal_builder().evaluate(
            symbol="SOL",
            side="long",
            trade_nature="trend_follow",
            health=TrendHealthResult(
                score=68,
                regime="weakening",
                components={},
                aligned_with_position=True,
                nature_adjusted_threshold=45,
            ),
            market_env={
                "price": 160,
                "orchestrator": {
                    "long_bias": "bullish",
                    "mid_bias": "bullish",
                    "short_bias": "bearish",
                },
                "indicators": {"swing_low": 145},
            },
        )

        assert result.level == "pullback"
        assert result.short_tf_flip is True
        assert result.long_tf_intact is True

    def test_structure_break_and_mid_flip_is_confirmed_reversal(self):
        from backend.services.reversal_signal_pack import get_reversal_signal_builder
        from backend.services.trend_health_score import TrendHealthResult

        result = get_reversal_signal_builder().evaluate(
            symbol="SOL",
            side="long",
            trade_nature="trend_follow",
            health=TrendHealthResult(
                score=30,
                regime="broken",
                components={},
                aligned_with_position=False,
                nature_adjusted_threshold=45,
            ),
            market_env={
                "price": 140,
                "orchestrator": {
                    "long_bias": "bullish",
                    "mid_bias": "bearish",
                    "short_bias": "bearish",
                },
                "indicators": {
                    "swing_low": 145,
                    "macd_hist": -0.3,
                    "macd_hist_prev": 0.2,
                },
            },
        )

        assert result.level == "confirmed_reversal"
        assert result.urgency >= 60
        assert any("结构" in item for item in result.evidence)
