"""TrendAgent scenario 落库与评分单测。"""
import pytest


pytestmark = pytest.mark.unit


class TestTrendPredictionScoring:
    def test_score_hit_on_profitable_long(self):
        from backend.services.trend_prediction_service import TrendPredictionService

        outcome, note = TrendPredictionService._score_record(
            side="long",
            entry_price=100.0,
            exit_price=108.0,
            pnl_pct=8.0,
            close_reason="take_profit",
            scenario_a="突破上涨",
            scenario_c="闪崩",
        )
        assert outcome == "hit"
        assert "主场景" in note or "盈利" in note

    def test_score_miss_on_wrong_direction(self):
        from backend.services.trend_prediction_service import TrendPredictionService

        outcome, _ = TrendPredictionService._score_record(
            side="long",
            entry_price=100.0,
            exit_price=95.0,
            pnl_pct=-5.0,
            close_reason="stop_loss",
            scenario_a="上涨",
            scenario_c="下跌",
        )
        assert outcome == "miss"


class TestTrendPredictionPersistence:
    def test_create_and_score_roundtrip(self, db_session):
        from backend.services.strategic_analyst.db_models import TrendPredictionRecord
        from backend.services.trend_prediction_service import trend_prediction_service

        record_id = trend_prediction_service.create_from_analysis(
            symbol="BTC",
            paper_position_id=42,
            entry_price=50000.0,
            analysis={
                "lifecycle": "加速",
                "scenario_a": "突破前高",
                "scenario_b": "震荡",
                "scenario_c": "闪崩",
            },
            db=db_session,
        )
        assert record_id is not None

        ok = trend_prediction_service.append_review_snapshot(
            paper_position_id=42,
            mark_price=52000.0,
            note="趋势仍在",
            db=db_session,
        )
        assert ok is True

        outcome = trend_prediction_service.score_on_close(
            paper_position_id=42,
            exit_price=53000.0,
            close_reason="take_profit",
            side="long",
            pnl_pct=6.0,
            db=db_session,
        )
        assert outcome == "hit"

        row = (
            db_session.query(TrendPredictionRecord)
            .filter(TrendPredictionRecord.paper_position_id == 42)
            .first()
        )
        assert row.scenario_a == "突破前高"
        assert row.outcome == "hit"
        import json
        snaps = json.loads(row.review_snapshots_json)
        assert len(snaps) == 1
