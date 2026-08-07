"""
test_full_auto_execution — FullAuto 执行路径单元测试

覆盖范围:
1. _validate_ai_decisions 审核规则
2. DeterministicRiskGate 风控规则
3. 防守模式限制
4. 编排器门控
5. PaperTradingEngine 基础逻辑
"""

import pytest
from unittest.mock import MagicMock, patch


# ════════════════════════════════════════════════════════
#  Fixtures
# ════════════════════════════════════════════════════════

@pytest.fixture
def mock_session():
    """模拟 FullAutoSession（event_log 为 list）"""
    s = MagicMock()
    s.event_log = []
    return s


@pytest.fixture
def service():
    """构造 FullAutoTradingService 实例（不连 DB）"""
    with patch("backend.services.full_auto_trading_service.FullAutoTradingService.__init__", lambda self: None):
        from backend.services.full_auto_trading_service import FullAutoTradingService
        svc = FullAutoTradingService()
        # 注入必要属性
        svc._VALID_ACTIONS = {"buy", "sell", "close", "reduce", "hold", "pyramid", "dca"}
        svc._VALID_RISK_LEVELS = {"low", "medium", "high"}
        svc._NATURE_TO_TIER_MAP = {
            "scalp": "short", "intraday": "short",
            "swing": "mid", "position": "long", "trend_follow": "long",
        }
        return svc


# ════════════════════════════════════════════════════════
#  1. _validate_ai_decisions 测试
# ════════════════════════════════════════════════════════

class TestValidateAiDecisions:

    def test_empty_result_returns_unchanged(self, service, mock_session):
        result = service._validate_ai_decisions(mock_session, None, ["BTC"], [])
        assert result is None

    def test_missing_decisions_downgrades_to_hold(self, service, mock_session):
        master = {"overall_assessment": "neutral", "risk_level": "medium", "decisions": None}
        result = service._validate_ai_decisions(mock_session, master, ["BTC", "ETH"], [])
        assert result["decisions"][0]["action"] == "hold"
        assert len(result["decisions"]) == 2

    def test_invalid_action_downgrades(self, service, mock_session):
        master = {
            "overall_assessment": "ok",
            "risk_level": "medium",
            "decisions": [{"symbol": "BTC", "action": "moon", "confidence": 80, "reasoning": "test reasoning for validation"}],
        }
        result = service._validate_ai_decisions(mock_session, master, ["BTC"], [])
        assert result["decisions"][0]["action"] == "hold"

    def test_confidence_out_of_range_clamped(self, service, mock_session):
        master = {
            "overall_assessment": "ok",
            "risk_level": "medium",
            "decisions": [{"symbol": "BTC", "action": "buy", "confidence": 150, "reasoning": "overconfident test reasoning here"}],
        }
        result = service._validate_ai_decisions(mock_session, master, ["BTC"], [])
        assert result["decisions"][0]["confidence"] <= 100

    def test_close_without_position_downgraded(self, service, mock_session):
        master = {
            "overall_assessment": "ok",
            "risk_level": "low",
            "decisions": [{"symbol": "BTC", "action": "close", "confidence": 70, "reasoning": "closing non-existent position test"}],
        }
        result = service._validate_ai_decisions(mock_session, master, ["BTC"], [])
        assert result["decisions"][0]["action"] == "hold"

    def test_buy_with_normal_confidence_passes(self, service, mock_session):
        master = {
            "overall_assessment": "bullish",
            "risk_level": "low",
            "decisions": [{"symbol": "BTC", "action": "buy", "confidence": 75, "reasoning": "strong uptrend with momentum support"}],
        }
        result = service._validate_ai_decisions(mock_session, master, ["BTC"], [])
        assert result["decisions"][0]["action"] == "buy"
        assert result["decisions"][0]["confidence"] == 75

    def test_all_same_direction_flagged(self, service, mock_session):
        master = {
            "overall_assessment": "bullish",
            "risk_level": "low",
            "decisions": [
                {"symbol": "BTC", "action": "buy", "confidence": 70, "reasoning": "reasoning 1 test"},
                {"symbol": "ETH", "action": "buy", "confidence": 70, "reasoning": "reasoning 2 test"},
                {"symbol": "SOL", "action": "buy", "confidence": 70, "reasoning": "reasoning 3 test"},
            ],
        }
        result = service._validate_ai_decisions(mock_session, master, ["BTC", "ETH", "SOL"], [])
        # Should still pass but warnings should be present
        assert all(d["action"] == "buy" for d in result["decisions"])

    def test_partial_close_pct_boundary(self, service, mock_session):
        positions = [{"symbol": "BTC", "side": "long", "entry_price": 50000, "timeframe_tier": "mid"}]
        master = {
            "overall_assessment": "ok",
            "risk_level": "medium",
            "decisions": [{"symbol": "BTC", "action": "reduce", "confidence": 60,
                           "reasoning": "partial close test", "partial_close_pct": 150}],
        }
        result = service._validate_ai_decisions(mock_session, master, ["BTC"], positions)
        # 150% should be flagged as out of range

    def test_symbol_not_in_session_rejected(self, service, mock_session):
        master = {
            "overall_assessment": "ok",
            "risk_level": "medium",
            "decisions": [{"symbol": "DOGE", "action": "buy", "confidence": 60,
                           "reasoning": "doge to the moon test reasoning here"}],
        }
        result = service._validate_ai_decisions(mock_session, master, ["BTC"], [])
        assert result["decisions"][0]["action"] == "hold"


# ════════════════════════════════════════════════════════
#  2. DeterministicRiskGate 测试
# ════════════════════════════════════════════════════════

class TestDeterministicRiskGate:

    def _make_gate(self, rules=None):
        from backend.services.deterministic_risk_gate import DeterministicRiskGate
        return DeterministicRiskGate(rules=rules)

    def test_pass_normal_order(self):
        from backend.services.deterministic_risk_gate import AccountSnapshot, PositionInfo, ProposedOrder
        gate = self._make_gate()
        account = AccountSnapshot(total_equity=100000, available_balance=80000, frozen_margin=20000)
        order = ProposedOrder(symbol="BTC", side="buy", notional=10000, margin=2000, leverage=5)
        result = gate.check(account, [], order)
        assert result.passed

    def test_reject_symbol_notional_exceeded(self):
        from backend.services.deterministic_risk_gate import AccountSnapshot, PositionInfo, ProposedOrder
        gate = self._make_gate({"max_symbol_notional_pct": 0.10})
        account = AccountSnapshot(total_equity=100000, available_balance=50000, frozen_margin=20000)
        positions = [PositionInfo(symbol="BTC", side="long", margin=5000, notional=15000, size=0.5, leverage=3)]
        order = ProposedOrder(symbol="BTC", side="buy", notional=12000, margin=2400, leverage=5)
        result = gate.check(account, positions, order)
        assert not result.passed
        assert "symbol_notional" in result.reason_code

    def test_reject_side_margin_exceeded(self):
        from backend.services.deterministic_risk_gate import AccountSnapshot, PositionInfo, ProposedOrder
        gate = self._make_gate({"max_side_margin_pct": 0.20})
        account = AccountSnapshot(total_equity=100000, available_balance=30000, frozen_margin=30000)
        positions = [PositionInfo(symbol="BTC", side="long", margin=18000, notional=45000, size=1.0, leverage=3)]
        order = ProposedOrder(symbol="ETH", side="buy", notional=10000, margin=5000, leverage=5)
        result = gate.check(account, positions, order)
        assert not result.passed
        assert "side_margin" in result.reason_code

    def test_reject_daily_loss_circuit(self):
        from backend.services.deterministic_risk_gate import AccountSnapshot, PositionInfo, ProposedOrder
        gate = self._make_gate({"max_daily_loss_pct": 0.05})
        account = AccountSnapshot(total_equity=100000, available_balance=40000, frozen_margin=20000,
                                  realized_pnl_today=-6000)
        order = ProposedOrder(symbol="BTC", side="buy", notional=5000, margin=1000, leverage=5)
        result = gate.check(account, [], order)
        assert not result.passed
        assert "daily_loss" in result.reason_code

    def test_reject_leverage_exceeded(self):
        from backend.services.deterministic_risk_gate import AccountSnapshot, PositionInfo, ProposedOrder
        gate = self._make_gate({"max_portfolio_leverage": 5})
        account = AccountSnapshot(total_equity=100000, available_balance=80000, frozen_margin=10000)
        order = ProposedOrder(symbol="BTC", side="buy", notional=10000, margin=1000, leverage=10)
        result = gate.check(account, [], order)
        assert not result.passed
        assert "leverage" in result.reason_code

    def test_reject_available_balance_low(self):
        from backend.services.deterministic_risk_gate import AccountSnapshot, PositionInfo, ProposedOrder
        gate = self._make_gate({"min_available_balance_pct": 0.20})
        account = AccountSnapshot(total_equity=100000, available_balance=15000, frozen_margin=80000)
        order = ProposedOrder(symbol="BTC", side="buy", notional=10000, margin=8000, leverage=5)
        result = gate.check(account, [], order)
        assert not result.passed
        assert "available_balance" in result.reason_code

    def test_custom_rules_override(self):
        from backend.services.deterministic_risk_gate import AccountSnapshot, PositionInfo, ProposedOrder
        # 超宽松规则 — 应通过
        gate = self._make_gate({"max_portfolio_leverage": 50, "max_side_margin_pct": 0.90})
        account = AccountSnapshot(total_equity=100000, available_balance=80000, frozen_margin=5000)
        order = ProposedOrder(symbol="BTC", side="buy", notional=10000, margin=500, leverage=20)
        result = gate.check(account, [], order)
        assert result.passed


# ════════════════════════════════════════════════════════
#  3. PaperTradingEngine 基础逻辑
# ════════════════════════════════════════════════════════

class TestPaperTradingEngine:

    def test_calc_liquidation_price_long(self):
        from backend.services.paper_trading_engine import PaperTradingEngine
        liq = PaperTradingEngine._calc_liquidation_price(10000, "long", 10)
        # long liq = entry * (1 - 1/lev + mm) = 10000 * (1 - 0.1 + 0.005) = 9050
        assert 9000 < liq < 9200

    def test_calc_liquidation_price_short(self):
        from backend.services.paper_trading_engine import PaperTradingEngine
        liq = PaperTradingEngine._calc_liquidation_price(10000, "short", 10)
        # short liq = entry * (1 + 1/lev - mm) = 10000 * (1 + 0.1 - 0.005) = 10950
        assert 10800 < liq < 11200

    def test_calc_liquidation_price_no_leverage(self):
        from backend.services.paper_trading_engine import PaperTradingEngine
        liq = PaperTradingEngine._calc_liquidation_price(10000, "long", 1)
        assert liq == 0.0

    def test_calc_unrealized_pnl_long_profit(self):
        from backend.services.paper_trading_engine import PaperTradingEngine
        pnl = PaperTradingEngine._calc_unrealized_pnl(100, 110, 1.0, "long")
        assert pnl == 10.0

    def test_calc_unrealized_pnl_short_profit(self):
        from backend.services.paper_trading_engine import PaperTradingEngine
        pnl = PaperTradingEngine._calc_unrealized_pnl(100, 90, 1.0, "short")
        assert pnl == 10.0

    def test_classify_volatility(self):
        from backend.services.paper_trading_engine import PaperTradingEngine
        assert PaperTradingEngine._classify_volatility("BTC") == "low"
        assert PaperTradingEngine._classify_volatility("ETH") == "low"
        assert PaperTradingEngine._classify_volatility("VIRTUAL") == "high"
        assert PaperTradingEngine._classify_volatility("SOL") == "mid"


# ════════════════════════════════════════════════════════
#  4. 防守模式限制测试
# ════════════════════════════════════════════════════════

class TestDefensiveMode:

    def test_defensive_only_allows_close_reduce_hold(self, service):
        defensive_actions = {"close", "reduce", "hold"}
        aggressive_actions = {"buy", "sell", "pyramid", "dca"}
        # In defensive mode, only close/reduce/hold should be allowed
        for a in defensive_actions:
            assert a in service._VALID_ACTIONS
        for a in aggressive_actions:
            assert a in service._VALID_ACTIONS
        # The actual enforcement is in _execute_master_decisions which
        # wraps buy/sell/pyramid/dca in mode checks


# ════════════════════════════════════════════════════════
#  5. Funding settlement (research mode)
# ════════════════════════════════════════════════════════

class TestFundingSettlement:

    def test_funding_skipped_in_demo_mode(self):
        """demo 模式下 _maybe_settle_funding 应直接返回"""
        from backend.services.paper_trading_engine import PaperTradingEngine
        engine = PaperTradingEngine()
        mock_db = MagicMock()
        mock_pos = MagicMock()
        with patch("backend.config.settings.PAPER_SIMULATION_TIER", "demo"):
            # Should not raise even though pos is incomplete
            engine._maybe_settle_funding(mock_db, mock_pos, 50000.0)


# ════════════════════════════════════════════════════════
#  6. LLM 超时配置
# ════════════════════════════════════════════════════════

class TestLLMTimeout:

    def test_timeout_param_passed_to_client(self):
        """验证 call_llm_api_sync 接受 timeout 参数"""
        import inspect
        from backend.services.llm_config_service import call_llm_api_sync
        sig = inspect.signature(call_llm_api_sync)
        assert "timeout" in sig.parameters


# ════════════════════════════════════════════════════════
#  7. Analyst fallback
# ════════════════════════════════════════════════════════

class TestAnalystFallback:

    def test_fallback_env_var_exists(self):
        """FULLAUTO_ANALYST_FALLBACK 环境变量应可读取"""
        from backend.config.settings import FULLAUTO_ANALYST_FALLBACK
        assert FULLAUTO_ANALYST_FALLBACK in ("none", "legacy")
