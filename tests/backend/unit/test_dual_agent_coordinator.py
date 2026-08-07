import pytest


class _MasterStub:
    def __init__(self):
        self.calls = 0

    def synthesize(self, **kwargs):
        self.calls += 1
        return {
            "overall_assessment": "master",
            "risk_level": "medium",
            "decisions": [
                {"symbol": "BTC", "action": "buy", "confidence": 66, "reasoning": "master entry", "trade_nature": "swing"},
                {"symbol": "ETH", "action": "hold", "confidence": 50, "reasoning": "master hold", "trade_nature": "trend_follow"},
            ],
        }


@pytest.mark.unit
class TestDualAgentCoordinator:
    def test_shadow_returns_master_and_attaches_dual_result(self, monkeypatch):
        import backend.config.settings as settings
        from backend.services.dual_agent_coordinator import dual_agent_coordinator

        monkeypatch.setattr(settings, "DUAL_AGENT_MODE", "shadow")
        monkeypatch.setattr(
            "backend.services.direction_agent.direction_agent.decide",
            lambda **kwargs: {
                "market_assessment": "dual",
                "decisions": [{"symbol": "BTC", "action": "sell", "confidence": 70, "reasoning": "dual"}],
            },
        )
        monkeypatch.setattr(
            "backend.services.trade_risk_agent.trade_risk_agent.review",
            lambda **kwargs: {
                "risk_level": "medium",
                "decisions": [{"symbol": "BTC", "action": "sell", "confidence": 70, "reasoning": "dual risk"}],
            },
        )

        master = _MasterStub()
        result = dual_agent_coordinator.coordinate(
            master_controller=master,
            reports={},
            symbols=["BTC"],
            portfolio={"positions": []},
            market_envs={},
        )

        assert result["decisions"][0]["action"] == "buy"
        assert "_dual_agent_shadow" in result
        assert master.calls == 1

    def test_advisory_only_replaces_exit_actions_for_open_positions(self, monkeypatch):
        import backend.config.settings as settings
        from backend.services.dual_agent_coordinator import dual_agent_coordinator

        monkeypatch.setattr(settings, "DUAL_AGENT_MODE", "advisory")
        monkeypatch.setattr(
            "backend.services.direction_agent.direction_agent.decide",
            lambda **kwargs: {
                "market_assessment": "dual",
                "decisions": [
                    {"symbol": "BTC", "action": "sell", "confidence": 80, "reasoning": "entry"},
                    {"symbol": "ETH", "action": "hold", "confidence": 50, "reasoning": "hold"},
                ],
            },
        )
        monkeypatch.setattr(
            "backend.services.trade_risk_agent.trade_risk_agent.review",
            lambda **kwargs: {
                "risk_level": "high",
                "decisions": [
                    {"symbol": "BTC", "action": "sell", "confidence": 80, "reasoning": "new entry"},
                    {"symbol": "ETH", "action": "reduce", "confidence": 75, "reasoning": "exit risk", "partial_close_pct": 30},
                ],
            },
        )

        result = dual_agent_coordinator.coordinate(
            master_controller=_MasterStub(),
            reports={},
            symbols=["BTC", "ETH"],
            portfolio={"positions": [{"symbol": "ETH", "side": "long"}]},
            market_envs={},
        )

        actions = {d["symbol"]: d["action"] for d in result["decisions"]}
        assert actions["BTC"] == "buy"      # advisory 不接管开仓
        assert actions["ETH"] == "reduce"   # 只接管已有仓退出

    def test_primary_uses_dual_and_risk_can_reject_entry(self, monkeypatch):
        import backend.config.settings as settings
        from backend.services.dual_agent_coordinator import dual_agent_coordinator

        monkeypatch.setattr(settings, "DUAL_AGENT_MODE", "primary")
        monkeypatch.setattr(
            "backend.services.direction_agent.direction_agent.decide",
            lambda **kwargs: {
                "market_assessment": "dual",
                "decisions": [{"symbol": "BTC", "action": "buy", "confidence": 80, "reasoning": "entry"}],
            },
        )
        monkeypatch.setattr(
            "backend.services.trade_risk_agent.trade_risk_agent.review",
            lambda **kwargs: {
                "risk_level": "high",
                "decisions": [{"symbol": "BTC", "action": "hold", "confidence": 65, "reasoning": "risk reject"}],
            },
        )

        result = dual_agent_coordinator.coordinate(
            master_controller=_MasterStub(),
            reports={},
            symbols=["BTC"],
            portfolio={"positions": []},
            market_envs={},
        )

        assert result["decisions"][0]["action"] == "hold"
        assert result["decisions"][0]["reasoning"] == "risk reject"
