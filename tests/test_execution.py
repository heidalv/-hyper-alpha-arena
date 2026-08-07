"""
P3.1 双轨执行 + P3.2 执行算法 + P3.4 熔断器 测试。

P3.1 完成标准：双轨同时跑 live+paper，ShadowDeviation 正确计算。
P3.2 完成标准：TWAP/POV/FundingIS/SOR 行为正确。
P3.4 完成标准：偏差连续 critical → 降仓/冻结（fail-closed）。
"""
from __future__ import annotations

import time

import pytest

from backend.services.contracts.types import (
    ApprovedTarget,
    Instrument,
    OrderEvent,
    OrderStatus,
)
from backend.services.execution.algo import (
    AlgoConfig,
    funding_is,
    pov,
    sor_route,
    twap,
)
from backend.services.execution.circuit_breaker import (
    CircuitBreakerConfig,
    CircuitState,
    ExecutionCircuitBreaker,
)
from backend.services.execution.client import (
    DualTrackExecutor,
    ExecutionClient,
    ShadowDeviation,
    _make_order_event,
)

pytestmark = pytest.mark.unit


def _inst(sym="BTC-PERP"):
    return Instrument(symbol=sym, venue="binance", kind="perp")


def _approved(qty=1.0, sym="BTC-PERP"):
    return ApprovedTarget(ts_ns=time.time_ns(), instrument=_inst(sym),
                          approved_qty=qty)


# ==================== P3.1 双轨执行 ====================

class _MockClient(ExecutionClient):
    """模拟执行 client（可控成交价）。"""
    def __init__(self, fill_price: float, name: str = "mock"):
        self.fill_price = fill_price
        self.name = name
        self.executed: list[ApprovedTarget] = []

    def execute(self, target: ApprovedTarget) -> OrderEvent:
        self.executed.append(target)
        return _make_order_event(
            target, OrderStatus.FILLED, self.fill_price,
            abs(target.approved_qty), fee=0.001 * abs(target.approved_qty) * self.fill_price,
            client_id=f"{self.name}_{target.ts_ns}",
        )


class TestDualTrackExecutor:
    def test_both_clients_execute(self):
        """R3：每个 target 同时进 live + paper。"""
        live = _MockClient(50000, "live")
        paper = _MockClient(50000, "paper")
        dual = DualTrackExecutor(live, paper)
        live_evt, paper_evt, dev = dual.execute_dual(_approved())
        assert len(live.executed) == 1
        assert len(paper.executed) == 1
        assert dev.severity == "OK"

    def test_deviation_computed(self):
        """成交价偏差正确计算。"""
        live = _MockClient(50010, "live")    # 偏离 10
        paper = _MockClient(50000, "paper")
        dual = DualTrackExecutor(live, paper, warn_dev_bps=5.0, critical_dev_bps=20.0)
        _, _, dev = dual.execute_dual(_approved())
        # 10/50000 * 1e4 = 2 bps → OK
        assert dev.price_dev_bps == pytest.approx(2.0, rel=0.1)
        assert dev.severity == "OK"

    def test_warn_deviation(self):
        live = _MockClient(50050, "live")    # 10 bps 偏差
        paper = _MockClient(50000, "paper")
        dual = DualTrackExecutor(live, paper, warn_dev_bps=5.0, critical_dev_bps=20.0)
        _, _, dev = dual.execute_dual(_approved())
        assert dev.price_dev_bps == pytest.approx(10.0, rel=0.1)
        assert dev.severity == "WARN"

    def test_critical_deviation(self):
        live = _MockClient(50200, "live")    # 40 bps 偏差
        paper = _MockClient(50000, "paper")
        dual = DualTrackExecutor(live, paper, warn_dev_bps=5.0, critical_dev_bps=20.0)
        _, _, dev = dual.execute_dual(_approved())
        assert dev.severity == "CRITICAL"

    def test_consecutive_critical_count(self):
        live = _MockClient(50200, "live")
        paper = _MockClient(50000, "paper")
        dual = DualTrackExecutor(live, paper, critical_dev_bps=20.0)
        for _ in range(3):
            dual.execute_dual(_approved())
        assert dual.consecutive_critical_count() == 3


# ==================== P3.2 执行算法 ====================

class TestTWAP:
    def test_equal_slices(self):
        children = twap(10.0, AlgoConfig(twap_slices=5, twap_interval_ms=500))
        assert len(children) == 5
        assert all(abs(c.qty - 2.0) < 1e-9 for c in children)

    def test_delays_uniform(self):
        children = twap(10.0, AlgoConfig(twap_slices=4, twap_interval_ms=1000))
        delays = [c.delay_ms for c in children]
        assert delays == [0, 1000, 2000, 3000]


class TestPOV:
    def test_participation_rate(self):
        """POV 按 5% 成交量参与。"""
        # 模拟线性成交量增长：100/min
        vol_fn = lambda elapsed_ms: elapsed_ms / 1000.0 * 100.0  # 100 per sec
        children = pov(50.0, vol_fn, AlgoConfig(pov_participation=0.05))
        total = sum(c.qty for c in children)
        assert total == pytest.approx(50.0, abs=1.0)
        assert len(children) >= 2

    def test_timeout_market_residual(self):
        """超时未完成，剩余市价。"""
        vol_fn = lambda ms: 0.0  # 无成交量
        children = pov(10.0, vol_fn, AlgoConfig(pov_participation=0.05, pov_max_duration_ms=3000))
        assert len(children) >= 1


class TestFundingIS:
    def test_high_funding_fewer_slices(self):
        """高 funding → 更少切片（尽快完成省 funding）。"""
        cfg = AlgoConfig(twap_slices=9)
        children_low, _ = funding_is(10.0, funding_rate_8h=0.0001, config=cfg)   # 1 bps
        children_high, _ = funding_is(10.0, funding_rate_8h=0.001, config=cfg)   # 10 bps
        assert len(children_high) <= len(children_low)

    def test_funding_cost_estimated(self):
        _, cost = funding_is(10.0, funding_rate_8h=0.001)
        assert cost > 0


class TestSOR:
    def test_routes_to_best_price(self):
        """SOR 路由到最优价 venue。"""
        quotes = {
            "binance": (50001, 5.0),
            "bybit": (50000, 3.0),    # 更优价
            "okx": (50002, 4.0),
        }
        routing = sor_route(8.0, quotes)
        # bybit 最优价，优先吃满
        assert routing.get("bybit", 0) == 3.0
        # 剩余 5.0 去次优 binance
        assert routing.get("binance", 0) == 5.0
        assert "okx" not in routing

    def test_partial_fill(self):
        quotes = {"a": (100, 2.0), "b": (101, 2.0)}
        routing = sor_route(5.0, quotes)
        assert sum(routing.values()) == pytest.approx(4.0)  # 只能吃 4


# ==================== P3.4 熔断器 ====================

class TestCircuitBreaker:
    def test_normal_by_default(self):
        cb = ExecutionCircuitBreaker()
        assert cb.state == CircuitState.NORMAL
        assert cb.can_open_position("BTC-PERP")

    def test_throttle_on_consecutive_critical(self):
        cb = ExecutionCircuitBreaker(CircuitBreakerConfig(consecutive_critical_to_throttle=3))
        for _ in range(3):
            cb.observe_deviation(ShadowDeviation(
                ts_ns=0, instrument_symbol="X", price_dev_bps=30,
                latency_dev_ms=0, fill_qty_diff=0, severity="CRITICAL",
            ))
        assert cb.state == CircuitState.THROTTLED
        assert cb.position_scale() == 0.5  # 降仓 50%

    def test_freeze_on_more_critical(self):
        cb = ExecutionCircuitBreaker(CircuitBreakerConfig(
            consecutive_critical_to_throttle=2, consecutive_critical_to_freeze=4))
        for _ in range(4):
            cb.observe_deviation(ShadowDeviation(
                ts_ns=0, instrument_symbol="X", price_dev_bps=30,
                latency_dev_ms=0, fill_qty_diff=0, severity="CRITICAL",
            ))
        assert cb.state == CircuitState.FROZEN
        assert not cb.can_open_position("X")  # fail-closed
        assert cb.position_scale() == 0.0

    def test_ok_resets_counter(self):
        cb = ExecutionCircuitBreaker()
        cb.observe_deviation(ShadowDeviation(0, "X", 30, 0, 0, "CRITICAL"))
        cb.observe_deviation(ShadowDeviation(0, "X", 1, 0, 0, "OK"))
        assert cb._consecutive_critical == 0

    def test_freeze_symbol(self):
        cb = ExecutionCircuitBreaker()
        cb.freeze_symbol("SOL-PERP", "data gap")
        assert not cb.can_open_position("SOL-PERP")
        assert cb.can_open_position("BTC-PERP")  # 其他品种不受影响

    def test_warn_ratio_throttle(self):
        """偏差占比超阈 → 降仓。"""
        cb = ExecutionCircuitBreaker(CircuitBreakerConfig(warn_ratio_to_throttle=0.5))
        for _ in range(15):
            cb.observe_deviation(ShadowDeviation(0, "X", 8, 0, 0, "WARN"))
        assert cb.state == CircuitState.THROTTLED
