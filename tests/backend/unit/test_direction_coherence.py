"""Direction Coherence Protocol (DCP) 单元测试."""

import os
import unittest

# 确保 enforce 模式
os.environ.setdefault("DIRECTION_COHERENCE_MODE", "enforce")

from backend.services.decision_core.direction_coherence import (
    evaluate_direction_coherence,
)


class TestDirectionCoherence(unittest.TestCase):
    def test_trend_follow_blocks_bearish_buy(self):
        v = evaluate_direction_coherence(
            action="buy",
            confidence=60,
            tier="long",
            trade_nature="trend_follow",
            orchestrator={
                "final_side": "short",
                "weighted_confidence": 0.35,
                "long_bias": "bearish",
                "long_conf": 0.4,
            },
            symbol="FARTCOIN",
        )
        self.assertFalse(v.allowed)
        self.assertIn("trend", v.rule)

    def test_swing_contrarian_high_conf_allowed_with_penalty(self):
        v = evaluate_direction_coherence(
            action="buy",
            confidence=80,
            tier="mid",
            trade_nature="swing",
            orchestrator={
                "final_side": "short",
                "weighted_confidence": 0.25,
                "mid_bias": "bearish",
                "mid_conf": 0.28,
            },
            symbol="FARTCOIN",
        )
        self.assertTrue(v.allowed)
        self.assertEqual(v.penalty, 10)
        self.assertEqual(v.rule, "contrarian_high_conf")

    def test_scalp_low_conf_blocks(self):
        v = evaluate_direction_coherence(
            action="sell",
            confidence=50,
            tier="short",
            trade_nature="scalp",
            orchestrator={
                "final_side": "long",
                "weighted_confidence": 0.55,
                "short_bias": "bullish",
                "short_conf": 0.54,
            },
            symbol="ZEC",
        )
        self.assertFalse(v.allowed)

    def test_neutral_aligned_allows(self):
        v = evaluate_direction_coherence(
            action="buy",
            confidence=60,
            tier="mid",
            trade_nature="swing",
            orchestrator={
                "final_side": "neutral",
                "weighted_confidence": 0.15,
                "mid_bias": "neutral",
                "mid_conf": 0.1,
            },
            symbol="BTC",
        )
        self.assertTrue(v.allowed)

    def test_fan_weak_oppose_blocks(self):
        v = evaluate_direction_coherence(
            action="buy",
            confidence=30,
            tier="mid",
            trade_nature="swing",
            orchestrator={"final_side": "neutral", "mid_bias": "bearish"},
            fan_branch="weak_oppose",
            symbol="FARTCOIN",
        )
        self.assertFalse(v.allowed)
        self.assertEqual(v.rule, "fan_weak_oppose")

    def test_macro_risk_off_blocks_trend_long(self):
        v = evaluate_direction_coherence(
            action="buy",
            confidence=85,
            tier="long",
            trade_nature="position",
            orchestrator={
                "macro_regime": "risk_off",
                "macro_phase_confidence": 0.7,
                "macro_cycle_phase": "decline",
            },
            symbol="BTC",
        )
        self.assertFalse(v.allowed)
        self.assertEqual(v.rule, "macro_regime_block_trend_long")


if __name__ == "__main__":
    unittest.main()
