"""Agent 维度绩效统计单测。"""
import pytest


pytestmark = pytest.mark.unit


class TestAgentAnalyticsService:
    def test_build_by_agent_report_merges_nature(self, monkeypatch):
        from backend.services import agent_analytics_service as svc

        fake_attr = {
            "by_nature": {
                "swing": {
                    "trades": 10, "wins": 6, "net_pnl": 100.0,
                    "gross_pnl": 110.0, "fees": 10.0,
                    "win_amount": 200.0, "loss_amount": 100.0,
                },
                "trend_follow": {
                    "trades": 5, "wins": 3, "net_pnl": 500.0,
                    "gross_pnl": 520.0, "fees": 20.0,
                    "win_amount": 600.0, "loss_amount": 100.0,
                },
                "position": {
                    "trades": 2, "wins": 1, "net_pnl": 50.0,
                    "gross_pnl": 52.0, "fees": 2.0,
                    "win_amount": 80.0, "loss_amount": 30.0,
                },
            },
        }

        class FakeFeedback:
            def build_net_attribution(self, db, days=30):
                return fake_attr

        monkeypatch.setattr(
            "backend.services.decision_feedback_service.decision_feedback_service",
            FakeFeedback(),
        )
        monkeypatch.setattr(svc, "_avg_hold_hours", lambda *a, **k: 4.5)
        monkeypatch.setattr(svc, "_scenario_hit_rate", lambda days: 0.55)

        report = svc.build_by_agent_report(None, days=30)
        assert report["days"] == 30
        assert "swing" in report["agents"]
        assert report["agents"]["swing"]["trades"] == 10
        assert report["agents"]["swing"]["win_rate"] == 0.6
        assert report["agents"]["swing"]["avg_hold_hours"] == 4.5

        trend = report["agents"]["trend_follow"]
        assert trend["trades"] == 7  # trend_follow + position merged
        assert trend["scenario_hit_rate"] == 0.55

    def test_build_by_agent_report_single_nature(self, monkeypatch):
        from backend.services import agent_analytics_service as svc

        class FakeFeedback:
            def build_net_attribution(self, db, days=7):
                return {
                    "by_nature": {
                        "swing": {
                            "trades": 3, "wins": 2, "net_pnl": 10.0,
                            "gross_pnl": 11.0, "fees": 1.0,
                            "win_amount": 15.0, "loss_amount": 5.0,
                        },
                    },
                }

        monkeypatch.setattr(
            "backend.services.decision_feedback_service.decision_feedback_service",
            FakeFeedback(),
        )
        monkeypatch.setattr(svc, "_avg_hold_hours", lambda *a, **k: None)

        report = svc.build_by_agent_report(None, days=7, nature="swing")
        assert list(report["agents"].keys()) == ["swing"]
        assert report["agents"]["swing"]["trades"] == 3

    def test_merge_nature_buckets_empty(self):
        from backend.services.agent_analytics_service import _merge_nature_buckets

        out = _merge_nature_buckets({}, ("swing",))
        assert out["trades"] == 0
        assert out["profit_factor"] is None
