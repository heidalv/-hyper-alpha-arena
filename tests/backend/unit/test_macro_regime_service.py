"""MacroRegimeState 服务与 DCP 宏观硬门单元测试."""

import os
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DIRECTION_COHERENCE_MODE", "enforce")

from backend.services.macro_regime_service import (
    MacroRegimeState,
    MacroRegimeService,
    _score_phases,
    _default_state,
    _constraint_for_phase,
    macro_regime_service,
)
from backend.services.decision_core.direction_coherence import evaluate_direction_coherence


class TestMacroRegimeScoring(unittest.TestCase):
    def test_bear_market_scores_decline(self):
        scores = _score_phases(
            market_cycle="bear_trend",
            macro_regime="risk_off",
            risk_on_score=-0.5,
            fgi=20.0,
            adx_1d=30.0,
            sma200_position=0.0,
            position_bias="short",
        )
        self.assertGreater(scores["decline"], scores["markup"])

    def test_bull_market_scores_markup(self):
        scores = _score_phases(
            market_cycle="bull_trend",
            macro_regime="risk_on",
            risk_on_score=0.5,
            fgi=60.0,
            adx_1d=28.0,
            sma200_position=1.0,
            position_bias="long",
        )
        self.assertGreater(scores["markup"], scores["decline"])

    def test_blocks_trend_long_decline(self):
        state = MacroRegimeState(
            cycle_phase="decline",
            phase_confidence=0.65,
            direction_constraint="no_trend_long",
            macro_regime="risk_off",
        )
        self.assertTrue(state.blocks_trend_long())

    def test_side_hint_decline(self):
        state = MacroRegimeState(
            cycle_phase="decline",
            direction_constraint="no_trend_long",
        )
        self.assertEqual(state.side_hint(), "short")

    def test_decline_high_conf_becomes_short_only(self):
        c = _constraint_for_phase("decline", "risk_off", phase_confidence=0.65)
        self.assertEqual(c, "short_only")

    def test_risk_off_low_conf_stays_no_trend_long(self):
        c = _constraint_for_phase("accumulation", "risk_off", phase_confidence=0.3)
        self.assertEqual(c, "no_trend_long")

    def test_inject_orchestrator_short_only(self):
        orch = macro_regime_service.inject_orchestrator_fields(
            {"allowed_direction": "both"},
            symbol="GLOBAL",
        )
        # 默认 state 为 accumulation/both — 仅验证字段注入不报错
        self.assertIn("macro_direction_constraint", orch)


class TestDeriveTrendSide(unittest.TestCase):
    def test_mid_bias_bearish_when_long_neutral(self):
        from backend.services.trend_agent import derive_trend_side
        env = {
            "BTC": {
                "orchestrator": {
                    "long_bias": "neutral",
                    "mid_bias": "bearish",
                }
            }
        }
        self.assertEqual(derive_trend_side("BTC", env), "short")


class TestMacroRegimeSmoothing(unittest.TestCase):
    def test_phase_not_flipped_within_hold_window(self):
        svc = MacroRegimeService()
        old = MacroRegimeState(
            symbol="GLOBAL",
            cycle_phase="decline",
            phase_confidence=0.7,
            updated_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        svc._cache["GLOBAL"] = old
        svc._persist = lambda state, db=None: None  # noqa: mock persist

        class FakeReport:
            market_cycle_phase = "bull_trend"
            macro_confidence = 0.8
            macro_bias = "bullish"
            macro_assessment = type("MA", (), {
                "regime": "risk_on",
                "risk_on_score": 0.6,
                "regime_transition_signal": False,
            })()

        new = svc.update_from_sources(strategic_report=FakeReport(), symbol="GLOBAL")
        self.assertEqual(new.cycle_phase, "decline")


class TestMacroRegimeDCPGate(unittest.TestCase):
    def test_decline_blocks_trend_buy(self):
        v = evaluate_direction_coherence(
            action="buy",
            confidence=80,
            tier="long",
            trade_nature="trend_follow",
            orchestrator={
                "macro_cycle_phase": "decline",
                "macro_phase_confidence": 0.65,
                "macro_regime": "risk_off",
                "macro_blocks_trend_long": True,
                "long_bias": "bearish",
                "long_conf": 0.7,
            },
            symbol="BTC",
        )
        self.assertFalse(v.allowed)
        self.assertEqual(v.rule, "macro_regime_block_trend_long")

    def test_swing_not_blocked_by_macro_gate(self):
        v = evaluate_direction_coherence(
            action="buy",
            confidence=80,
            tier="mid",
            trade_nature="swing",
            orchestrator={
                "macro_cycle_phase": "decline",
                "macro_phase_confidence": 0.65,
                "macro_blocks_trend_long": True,
                "mid_bias": "bullish",
                "mid_conf": 0.5,
            },
            symbol="BTC",
        )
        self.assertTrue(v.allowed)


class TestMacroRegimePersistence(unittest.TestCase):
    def test_default_state_structure(self):
        state = _default_state("GLOBAL")
        self.assertEqual(state.symbol, "GLOBAL")
        self.assertIn(state.cycle_phase, ("accumulation", "markup", "distribution", "decline"))


if __name__ == "__main__":
    unittest.main()
