"""
DeterministicRiskGate 单元测试（v3 整改已对齐）

覆盖现行 API `check(account, positions, order) -> RiskCheckResult`：
- Rule 1: 单品种敞口占比
- Rule 2: 单侧保证金占比
- Rule 3: 单日亏损熔断
- Rule 4: 最大杠杆
- Rule 5: 最小可用余额
"""
import pytest


def _snapshot(**kw):
    from backend.services.deterministic_risk_gate import AccountSnapshot
    defaults = {
        "total_equity": 10000.0,
        "available_balance": 9000.0,
        "frozen_margin": 0.0,
        "realized_pnl_today": 0.0,
    }
    defaults.update(kw)
    return AccountSnapshot(**defaults)


def _order(**kw):
    from backend.services.deterministic_risk_gate import ProposedOrder
    defaults = {
        "symbol": "BTC",
        "side": "buy",
        "notional": 1000.0,
        "margin": 100.0,
        "leverage": 10.0,
    }
    defaults.update(kw)
    return ProposedOrder(**defaults)


def _pos(**kw):
    from backend.services.deterministic_risk_gate import PositionInfo
    defaults = {
        "symbol": "BTC",
        "side": "long",
        "margin": 0.0,
        "notional": 0.0,
        "size": 0.0,
        "leverage": 10.0,
    }
    defaults.update(kw)
    return PositionInfo(**defaults)


@pytest.mark.unit
class TestDeterministicRiskGate:
    @pytest.fixture(autouse=True)
    def setup(self):
        from backend.services.deterministic_risk_gate import DeterministicRiskGate
        self.gate = DeterministicRiskGate()

    def test_normal_order_passes(self):
        result = self.gate.check(_snapshot(), [], _order())
        assert result.passed is True

    def test_side_margin_blocked_on_pyramiding(self):
        # 已有同向仓位占用 50% 保证金，再来一个 5% 应触发 max_side_margin_pct
        existing = [_pos(side="long", margin=5000.0, notional=50000.0)]
        result = self.gate.check(_snapshot(), existing, _order(margin=500.0))
        assert result.passed is False
        assert "side" in result.reason_code or "symbol" in result.reason_code

    def test_leverage_exceeds_max_blocked(self):
        result = self.gate.check(_snapshot(), [], _order(leverage=100.0))
        assert result.passed is False
        assert result.reason_code == "leverage_exceeded"

    def test_daily_loss_circuit_trips(self):
        # 全局日亏损当前是 15% 极端安全网；更细的 per-symbol 日亏损由上层传入 symbol_daily_pnl 处理。
        acct = _snapshot(realized_pnl_today=-1600.0)  # 16% 亏损
        result = self.gate.check(acct, [], _order())
        assert result.passed is False
        assert result.reason_code == "daily_loss_circuit"

    def test_available_balance_low_blocked(self):
        # 可用余额不足覆盖新单 margin + 规则下限
        acct = _snapshot(total_equity=10000.0, available_balance=100.0)
        result = self.gate.check(acct, [], _order(margin=80.0))
        assert result.passed is False
        assert result.reason_code == "available_balance_low"

    def test_reduce_order_basic(self):
        # 开仓校验对 side=buy/sell 都走同一路径；卖出平仓类场景：side="sell"，positions 里有 long
        existing = [_pos(side="long", margin=200.0, notional=2000.0, size=0.02)]
        # 反向开空同品种，只要不爆侧敞口即应通过
        result = self.gate.check(_snapshot(), existing, _order(side="sell", margin=100.0))
        assert result.passed is True
