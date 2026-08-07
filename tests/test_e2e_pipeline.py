"""
端到端流水线测试（L2→L3→L4→L5 全链真实数据流）。

这不是 import 冒烟——是合成 market snapshot 流过完整管线：
    MarketSnapshot → FactorCompute → AlphaEnsemble → MetaLabel
    → PortfolioConstruction → RiskGate → DualTrackExecutor → OrderEvent
    + Cache 事件溯源 + replay 回放 + 熔断联动

验证各胶水层 + 契约层 + 执行层 + 缓存层真正协同工作。
"""
from __future__ import annotations

import pytest

from backend.services.alpha.ensemble import AlphaEnsemble
from backend.services.alpha.regime_refined import Regime
from backend.services.cache.single_source import SingleSourceCache
from backend.services.contracts.types import (
    ApprovedTarget,
    DataQuality,
    Direction,
    FactorVector,
    Horizon,
    Insight,
    Instrument,
    MarketSnapshot,
    Target,
)
from backend.services.execution.backtest_client import (
    BacktestExecutionClient,
    FillModel,
)
from backend.services.execution.circuit_breaker import (
    CircuitBreakerConfig,
    ExecutionCircuitBreaker,
)
from backend.services.execution.client import (
    DualTrackExecutor,
    ExecutionClient,
    _make_order_event,
)
from backend.services.portfolio.construction import PortfolioConfig, PortfolioConstructionAgent
from backend.services.portfolio.risk_gate import RiskGateAgent, RiskGateConfig

pytestmark = pytest.mark.unit


# ==================== 辅助：模拟各层 ====================

class _MockFactorCompute:
    """模拟 FactorCompute：snapshot → FactorVector。"""
    def __init__(self, signal_strength: float = 0.5):
        self.signal = signal_strength

    def compute(self, snap: MarketSnapshot) -> FactorVector:
        return FactorVector(
            ts_ns=snap.ts_ns, instrument=snap.instrument,
            values={"momentum": self.signal, "mean_rev": -self.signal * 0.5},
        )


class _MockAlphaModel:
    """模拟 AlphaEnsemble 子模型：FactorVector → (direction, confidence)。"""
    def __init__(self, name, direction_bias, confidence):
        self.name = name
        self.direction_bias = direction_bias
        self.confidence = confidence

    def predict_direction(self, fv: FactorVector):
        mom = fv.values.get("momentum", 0)
        if self.direction_bias == "momentum":
            d = Direction.LONG if mom > 0 else Direction.SHORT
        elif self.direction_bias == "meanrev":
            mr = fv.values.get("mean_rev", 0)
            d = Direction.LONG if mr > 0 else Direction.SHORT
        else:
            d = Direction.FLAT
        return d, self.confidence


def _make_snap(ts, price=50000, sym="BTC-PERP"):
    inst = Instrument(symbol=sym, venue="binance", kind="perp")
    return MarketSnapshot(
        ts_ns=ts, instrument=inst, bid=price - 0.5, ask=price + 0.5,
        mid=price, last_trade=price, last_trade_size=0.1,
        quality=DataQuality.OK,
    )


class _FixedPriceLive(ExecutionClient):
    """模拟 live client（固定成交价）。"""
    def __init__(self, price):
        self.price = price
        self.filled = []

    def execute(self, target):
        self.filled.append(target)
        return _make_order_event(
            target, __import__("backend.services.contracts.types", fromlist=["OrderStatus"]).OrderStatus.FILLED,
            self.price, abs(target.approved_qty), 0.0,
            client_id=f"live_{target.ts_ns}", side="buy" if target.approved_qty >= 0 else "sell",
        )


# ==================== 端到端流水线 ====================

class TestEndToEndPipeline:
    """真实数据流过完整管线。"""

    def test_full_chain_long_signal(self):
        """多头信号：snapshot → factor → insight(LONG) → target → approved → order。"""
        cache = SingleSourceCache()

        # L2: 生成 snapshot
        snap = _make_snap(1000, price=50000)
        cache.update_snapshot(snap)

        # L3: factor compute（momentum 与 mean_rev 同号，两模型一致看多）
        fc = _MockFactorCompute(signal_strength=0.8)
        fv = fc.compute(snap)
        # 让 mean_rev 也为正（momentum=0.8, mean_rev=+0.4）使两模型都看多
        fv = FactorVector(ts_ns=fv.ts_ns, instrument=fv.instrument,
                          values={"momentum": 0.8, "mean_rev": 0.4})
        cache.update_factor(fv)

        # L3: alpha ensemble（两个模型都看多，用 ensemble 默认权重的模型名）
        ens = AlphaEnsemble()
        ens.register(_MockAlphaModel("lightgbm", "momentum", 0.85))
        ens.register(_MockAlphaModel("online_linear", "meanrev", 0.70))
        pred = ens.predict(fv, regime=Regime.TREND_LOW_VOL.value)
        assert pred.direction == Direction.LONG

        # 构造 Insight（enemble 输出 → 契约 Insight）
        insight = Insight(
            ts_ns=1000, instrument=snap.instrument, direction=pred.direction,
            confidence=pred.confidence, magnitude=pred.magnitude,
            period_ns=3600_000_000_000, horizon=Horizon.SHORT,
            source="ensemble", expiry_ns=2_000_000_000_000,
        )
        cache.update_insight(insight)

        # L4: portfolio construction（insight → target）
        portfolio = PortfolioConstructionAgent(PortfolioConfig(
            total_budget_usd=100_000, price_oracle=lambda s: 50000))
        targets = portfolio.construct([insight])
        assert len(targets) == 1
        assert targets[0].target_qty > 0  # 多头
        cache.update_target(targets[0])

        # L4: risk gate（target → approved）
        gate = RiskGateAgent(RiskGateConfig(circuit_scale=1.0))
        approved = gate.review(targets)
        assert len(approved) == 1
        assert approved[0].approved_qty > 0
        cache.update_approved(approved[0])

        # L5: dual track execution
        live = _FixedPriceLive(50000)
        paper = _FixedPriceLive(50000)
        dual = DualTrackExecutor(live, paper)
        live_evt, paper_evt, dev = dual.execute_dual(approved[0])
        cache.append_order(live_evt)

        # 验证全链产物
        assert live_evt.status.value == "FILLED"
        assert paper_evt.status.value == "FILLED"
        assert dev.severity == "OK"  # live/paper 同价

        # 验证 Cache replay（R8）
        events = cache.replay(900, 1100)
        assert len(events) >= 5  # snapshot+factor+insight+target+approved+order

    def test_risk_gate_reduces_on_circuit(self):
        """熔断（circuit_scale=0.5）→ 风控削减仓位。"""
        insight = Insight(
            ts_ns=1000, instrument=Instrument(symbol="BTC-PERP", venue="binance", kind="perp"),
            direction=Direction.LONG, confidence=0.8, magnitude=0.02,
            period_ns=3600_000_000_000, horizon=Horizon.SHORT,
            source="test", expiry_ns=2e12,
        )
        portfolio = PortfolioConstructionAgent(PortfolioConfig(
            total_budget_usd=100_000, price_oracle=lambda s: 50000))
        targets = portfolio.construct([insight])
        full_qty = targets[0].target_qty

        # 熔断降仓 50%
        gate = RiskGateAgent(RiskGateConfig(circuit_scale=0.5))
        approved = gate.review(targets)
        assert len(approved) == 1
        assert abs(approved[0].approved_qty) < abs(full_qty)

    def test_risk_gate_freezes_symbol(self):
        """品种冻结（数据 GAP）→ 拒绝该品种新开仓。"""
        inst = Instrument(symbol="SOL-PERP", venue="binance", kind="perp")
        target = Target(ts_ns=1000, instrument=inst, target_qty=2.0)
        gate = RiskGateAgent(RiskGateConfig())
        gate.freeze_symbol("SOL-PERP")
        approved = gate.review([target])
        assert len(approved) == 0  # 冻结 → 拒绝

    def test_risk_gate_rejects_on_full_freeze(self):
        """熔断 circuit_scale=0 → 全拒（fail-closed）。"""
        inst = Instrument(symbol="BTC-PERP", venue="binance", kind="perp")
        target = Target(ts_ns=1000, instrument=inst, target_qty=1.0)
        gate = RiskGateAgent(RiskGateConfig(circuit_scale=0.0))
        approved = gate.review([target])
        assert len(approved) == 0

    def test_low_confidence_filtered(self):
        """低置信 insight 不产生 target。"""
        insight = Insight(
            ts_ns=1000, instrument=Instrument(symbol="BTC-PERP", venue="binance", kind="perp"),
            direction=Direction.LONG, confidence=0.1, magnitude=0.001,  # 低置信
            period_ns=3600_000_000_000, horizon=Horizon.SHORT,
            source="test", expiry_ns=2e12,
        )
        portfolio = PortfolioConstructionAgent(PortfolioConfig(
            min_confidence_to_trade=0.3, total_budget_usd=100_000))
        targets = portfolio.construct([insight])
        assert len(targets) == 0  # 过滤

    def test_backtest_parity_in_pipeline(self):
        """回测 parity 在管线中：同 target 过 live + backtest client。"""
        inst = Instrument(symbol="BTC-PERP", venue="binance", kind="perp")
        target = ApprovedTarget(ts_ns=1000, instrument=inst, approved_qty=1.0)

        live = _FixedPriceLive(50005)
        def oracle(ts, sym):
            return 50000.0, 1e9
        bt = BacktestExecutionClient(price_oracle=oracle,
                                     fill_model=FillModel(slippage_bps=0.5))
        from backend.services.execution.backtest_client import run_parity_check
        result = run_parity_check([target], live, bt,
                                  max_price_dev_bps=50, max_fill_qty_diff_pct=0.2)
        assert result["n_compared"] == 1
        # parity 偏差量化（live 50005 vs backtest ~50002.5）
        assert result["max_price_dev_bps"] < 50

    def test_circuit_breaker_triggers_from_deviations(self):
        """连续 critical 偏差 → 熔断 → 风控联动。"""
        cb = ExecutionCircuitBreaker(CircuitBreakerConfig(
            consecutive_critical_to_throttle=2, consecutive_critical_to_freeze=4))
        from backend.services.execution.client import ShadowDeviation
        # 注入连续 critical
        for _ in range(3):
            cb.observe_deviation(ShadowDeviation(
                ts_ns=0, instrument_symbol="BTC", price_dev_bps=30,
                latency_dev_ms=0, fill_qty_diff=0, severity="CRITICAL"))

        gate = RiskGateAgent(RiskGateConfig())
        # 联动：把熔断仓位倍数同步给风控
        gate.set_circuit_scale(cb.position_scale())
        assert cb.state.value == "THROTTLED"
        assert gate.config.circuit_scale == 0.5  # 降仓

    def test_mixed_signals_short_and_long(self):
        """混合多空信号：portfolio 分别处理，risk gate 不混淆。"""
        inst_btc = Instrument(symbol="BTC-PERP", venue="binance", kind="perp")
        inst_eth = Instrument(symbol="ETH-PERP", venue="binance", kind="perp")
        insights = [
            Insight(ts_ns=1000, instrument=inst_btc, direction=Direction.LONG,
                    confidence=0.8, magnitude=0.02, period_ns=3600_000_000_000,
                    horizon=Horizon.SHORT, source="t", expiry_ns=2e12),
            Insight(ts_ns=1000, instrument=inst_eth, direction=Direction.SHORT,
                    confidence=0.7, magnitude=0.015, period_ns=3600_000_000_000,
                    horizon=Horizon.SHORT, source="t", expiry_ns=2e12),
        ]
        portfolio = PortfolioConstructionAgent(PortfolioConfig(
            total_budget_usd=100_000,
            price_oracle=lambda s: 50000 if "BTC" in s else 3000))
        targets = portfolio.construct(insights)
        assert len(targets) == 2
        btc_t = [t for t in targets if "BTC" in t.instrument.symbol][0]
        eth_t = [t for t in targets if "ETH" in t.instrument.symbol][0]
        assert btc_t.target_qty > 0   # 多
        assert eth_t.target_qty < 0   # 空
