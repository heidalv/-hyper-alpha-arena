"""
P5 中长线协同 测试（Alpha Bus + MidLong Agent + HedgeLedger + Portfolio + CrossHorizon Breaker）。
"""
from __future__ import annotations

import time

import pytest

from backend.services.agents.midlong.agent import MidLongAgent, MidLongConfig
from backend.services.alpha.regime_refined import Regime
from backend.services.bus.alpha_bus import AlphaBus, Thesis
from backend.services.contracts.types import Direction, Horizon, Insight, Instrument, RegimeLabel
from backend.services.portfolio.cross_horizon_breaker import (
    CrossHorizonCircuitBreaker,
    CrossHorizonState,
)
from backend.services.portfolio.unified import (
    HedgeLedger,
    RiskBudgetConfig,
    UnifiedPortfolio,
)
from backend.services.portfolio.unified import (
    Horizon as PHorizon,
)

pytestmark = pytest.mark.unit


def _inst(sym="BTC-PERP"):
    return Instrument(symbol=sym, venue="binance", kind="perp")


def _insight(direction=Direction.LONG, confidence=0.8, horizon=Horizon.SHORT, sym="BTC-PERP"):
    return Insight(ts_ns=time.time_ns(), instrument=_inst(sym), direction=direction,
                   confidence=confidence, magnitude=0.02, period_ns=3600_000_000_000,
                   horizon=horizon, source="test", expiry_ns=2_000_000_000_000)


# ==================== P5.1 Alpha Bus ====================

class TestAlphaBus:
    def test_publish_subscribe_insight(self):
        bus = AlphaBus()
        received = []
        bus.subscribe("insight_short", received.append)
        bus.publish_insight(_insight())
        assert len(received) == 1

    def test_publish_thesis(self):
        bus = AlphaBus()
        received = []
        bus.subscribe("thesis_mid", received.append)
        t = Thesis(ts_ns=0, instrument_symbol="BTC-PERP", horizon=Horizon.MID,
                   direction="long", conviction=0.7, target_weight=0.1,
                   time_window_ns=1e12, rationale="trend")
        bus.publish_thesis(t)
        assert len(received) == 1

    def test_topic_isolation(self):
        """短/中/长 topic 隔离，不互相串扰。"""
        bus = AlphaBus()
        short_recv, mid_recv = [], []
        bus.subscribe("insight_short", short_recv.append)
        bus.subscribe("thesis_mid", mid_recv.append)
        bus.publish_insight(_insight())
        assert len(short_recv) == 1
        assert len(mid_recv) == 0  # 中线不收短线

    def test_latest_query(self):
        bus = AlphaBus()
        bus.publish_insight(_insight(sym="ETH-PERP"))
        latest = bus.latest_insight(Horizon.SHORT, "ETH-PERP")
        assert latest is not None
        assert latest.instrument.symbol == "ETH-PERP"

    def test_regime_broadcast(self):
        bus = AlphaBus()
        recv = []
        bus.subscribe("regime", recv.append)
        bus.publish_regime(RegimeLabel(ts_ns=0, regime="trend_high_vol", confidence=0.8))
        assert len(recv) == 1


# ==================== P5.2 MidLong Agent ====================

class TestMidLongAgent:
    def test_generate_thesis_publishes(self):
        bus = AlphaBus()
        received = []
        bus.subscribe("thesis_mid", received.append)
        agent = MidLongAgent(bus, MidLongConfig(use_short_overlay=False))
        thesis = agent.generate_thesis("BTC-PERP", "long", 0.7, 0.05, "trend up")
        assert thesis is not None
        assert len(received) == 1
        assert thesis.direction == "long"

    def test_max_theses_limit(self):
        bus = AlphaBus()
        agent = MidLongAgent(bus, MidLongConfig(max_theses=2, use_short_overlay=False))
        agent.generate_thesis("BTC", "long", 0.5, 0.05)
        agent.generate_thesis("ETH", "long", 0.5, 0.05)
        result = agent.generate_thesis("SOL", "long", 0.5, 0.05)  # 超限
        assert result is None

    def test_short_overlay_detects_conflict(self):
        """短线逆势 vs 中长线 thesis → 标记（不崩）。"""
        bus = AlphaBus()
        agent = MidLongAgent(bus, MidLongConfig(use_short_overlay=True))
        agent.generate_thesis("BTC-PERP", "long", 0.8, 0.05)
        # 发短线 SHORT insight（与 thesis 相反）
        bus.publish_insight(_insight(direction=Direction.SHORT, confidence=0.7))
        # 不崩即可（日志标记对冲检查）
        assert len(agent.active_theses()) == 1

    def test_expire_stale(self):
        bus = AlphaBus()
        agent = MidLongAgent(bus, MidLongConfig(use_short_overlay=False))
        agent.generate_thesis("BTC", "long", 0.5, 0.05, ts_ns=0)
        # 很久以后
        expired = agent.expire_stale(now_ns=10**19)
        assert expired >= 1
        assert len(agent.active_theses()) == 0


# ==================== P5.3 HedgeLedger + Portfolio ====================

class TestHedgeLedger:
    def test_net_exposure(self):
        ledger = HedgeLedger()
        ledger.update(PHorizon.SHORT, "BTC", 1.0)
        ledger.update(PHorizon.MID, "BTC", 2.0)
        assert ledger.net_exposure("BTC") == 3.0

    def test_conflict_detection(self):
        """短线空 vs 中长线多 → 冲突告警。"""
        ledger = HedgeLedger()
        ledger.update(PHorizon.SHORT, "BTC", -1.0)  # 短线空
        ledger.update(PHorizon.MID, "BTC", 2.0)      # 中线多
        alert = ledger.check_conflict("BTC")
        assert alert is not None
        assert alert.short_horizon_dir == "short"

    def test_no_conflict_aligned(self):
        """方向一致 → 无告警。"""
        ledger = HedgeLedger()
        ledger.update(PHorizon.SHORT, "BTC", 1.0)
        ledger.update(PHorizon.LONG, "BTC", 1.0)
        assert ledger.check_conflict("BTC") is None


class TestUnifiedPortfolio:
    def test_allocate_by_sharpe(self):
        portfolio = UnifiedPortfolio()
        metrics = {
            PHorizon.SHORT: {"sharpe": 2.0, "capacity_usd": 1e6},
            PHorizon.MID: {"sharpe": 1.0, "capacity_usd": 1e7},
            PHorizon.LONG: {"sharpe": 1.5, "capacity_usd": 1e7},
            PHorizon.SCALP: {"sharpe": 3.0, "capacity_usd": 5e5},
        }
        weights = portfolio.allocate(metrics, current_drawdown=0.0)
        assert sum(weights.values()) <= 1.0 + 1e-9
        # scalp sharpe 高 → 权重应非零
        assert weights[PHorizon.SCALP] > 0

    def test_drawdown_deleverage(self):
        """回撤超阈 → 降仓。"""
        portfolio = UnifiedPortfolio(RiskBudgetConfig(max_drawdown_pct=0.10, drawdown_deleverage=0.5))
        metrics = {PHorizon.SHORT: {"sharpe": 2.0, "capacity_usd": 1e6},
                   PHorizon.MID: {"sharpe": 1.0, "capacity_usd": 1e7},
                   PHorizon.LONG: {"sharpe": 1.0, "capacity_usd": 1e7},
                   PHorizon.SCALP: {"sharpe": 1.0, "capacity_usd": 1e6}}
        weights_normal = sum(portfolio.allocate(metrics, current_drawdown=0.0).values())
        weights_dd = sum(portfolio.allocate(metrics, current_drawdown=0.15).values())
        assert weights_dd < weights_normal  # 回撤后权重更低


# ==================== P5.4 CrossHorizon Breaker ====================

class TestCrossHorizonBreaker:
    def test_normal(self):
        ledger = HedgeLedger()
        cb = CrossHorizonCircuitBreaker(ledger)
        cb.assess(total_exposure_usd=50000, net_equity_usd=100000,
                  regime=Regime.RANGE.value, portfolio_drawdown=0.0)
        assert cb.state == CrossHorizonState.NORMAL
        assert cb.target_exposure_scale() == 1.0

    def test_reduced_on_high_exposure(self):
        ledger = HedgeLedger()
        cb = CrossHorizonCircuitBreaker(ledger)
        cb.assess(total_exposure_usd=90000, net_equity_usd=100000,
                  regime=Regime.RANGE.value, portfolio_drawdown=0.0)
        assert cb.state == CrossHorizonState.REDUCED
        assert cb.target_exposure_scale() == 0.5

    def test_emergency_on_liquidation_cascade(self):
        """连环清算 → EMERGENCY 全平。"""
        ledger = HedgeLedger()
        cb = CrossHorizonCircuitBreaker(ledger)
        cb.assess(total_exposure_usd=50000, net_equity_usd=100000,
                  regime=Regime.LIQUIDATION_CASCADE.value, portfolio_drawdown=0.0)
        assert cb.state == CrossHorizonState.EMERGENCY
        assert cb.target_exposure_scale() == 0.0

    def test_emergency_on_drawdown(self):
        """组合回撤超阈 → EMERGENCY。"""
        ledger = HedgeLedger()
        cb = CrossHorizonCircuitBreaker(ledger)
        cb.assess(total_exposure_usd=50000, net_equity_usd=100000,
                  regime=Regime.RANGE.value, portfolio_drawdown=0.25)
        assert cb.state == CrossHorizonState.EMERGENCY

    def test_recovery_to_normal(self):
        """极端解除后恢复正常。"""
        ledger = HedgeLedger()
        cb = CrossHorizonCircuitBreaker(ledger)
        cb.assess(50000, 100000, regime=Regime.EXTREME.value, portfolio_drawdown=0.0)
        assert cb.state == CrossHorizonState.EMERGENCY
        cb.assess(50000, 100000, regime=Regime.RANGE.value, portfolio_drawdown=0.0)
        assert cb.state == CrossHorizonState.NORMAL
