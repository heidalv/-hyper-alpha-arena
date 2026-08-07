"""
P2.1 Lean 5 层契约测试。

完成标准（方案 P2.1）：
    - 全部契约 dataclass 可构造、不可变
    - 桥接层正确互转新旧 MarketSnapshot
    - 契约层零业务依赖（可独立编译）
    - 契约检查器强制启用
"""
from __future__ import annotations

import dataclasses

import pytest

from backend.services.contracts.bridge import (
    contract_to_legacy,
    legacy_to_contract,
    make_instrument,
)
from backend.services.contracts.types import (
    ApprovedTarget,
    DataQuality,
    Direction,
    FactorVector,
    Horizon,
    Insight,
    Instrument,
    MarketSnapshot,
    OrderAlgo,
    OrderEvent,
    OrderStatus,
    OrderUrgency,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def instrument():
    return Instrument(symbol="BTC-PERP", venue="hyperliquid", kind="perp",
                      tick_size=0.1, lot_size=0.001, adv_usd=1e9)


class TestImmutability:
    """所有契约 dataclass 必须 frozen=True（事件溯源友好）。"""

    def test_instrument_frozen(self, instrument):
        with pytest.raises(dataclasses.FrozenInstanceError):
            instrument.symbol = "ETH-PERP"

    def test_market_snapshot_frozen(self, instrument):
        snap = MarketSnapshot(
            ts_ns=1, instrument=instrument, bid=50000, ask=50001,
            mid=50000.5, last_trade=50000, last_trade_size=0.5,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            snap.mid = 51000

    def test_insight_frozen(self, instrument):
        ins = Insight(
            ts_ns=1, instrument=instrument, direction=Direction.LONG,
            confidence=0.8, magnitude=0.02, period_ns=3600_000_000_000,
            horizon=Horizon.SHORT, source="lgbm", expiry_ns=2_000_000_000_000,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            ins.confidence = 0.5


class TestConstruction:
    def test_market_snapshot_with_l2(self, instrument):
        snap = MarketSnapshot(
            ts_ns=1, instrument=instrument, bid=50000, ask=50001,
            mid=50000.5, last_trade=50000, last_trade_size=0.5,
            l2=((50000, 1.5), (50001, 2.0)),
            seq=100, quality=DataQuality.OK,
        )
        assert len(snap.l2) == 2
        assert snap.seq == 100

    def test_factor_vector_defaults(self, instrument):
        fv = FactorVector(ts_ns=1, instrument=instrument)
        assert fv.values == {}
        assert fv.expr_ids == {}

    def test_insight_enums(self, instrument):
        ins = Insight(
            ts_ns=1, instrument=instrument, direction=Direction.SHORT,
            confidence=0.6, magnitude=-0.01, period_ns=1800_000_000_000,
            horizon=Horizon.SCALP, source="sac", expiry_ns=1_000_000_000_000,
        )
        assert ins.direction == Direction.SHORT
        assert ins.horizon == Horizon.SCALP

    def test_approved_target_gate_log(self, instrument):
        at = ApprovedTarget(
            ts_ns=1, instrument=instrument, approved_qty=0.5,
            algo=OrderAlgo.TWAP, urgency=OrderUrgency.HIGH,
            gate_log=("risk_engine", "capacity"),
        )
        assert len(at.gate_log) == 2

    def test_order_event_status(self, instrument):
        oe = OrderEvent(
            ts_ns=1, instrument=instrument, client_id="c1",
            venue_order_id="v1", side="buy", price=50000, qty=0.1,
            status=OrderStatus.FILLED, fill_price=50000, fill_qty=0.1,
        )
        assert oe.status == OrderStatus.FILLED


class TestBridge:
    def test_legacy_to_contract(self):
        """旧 MarketSnapshot → 新契约（补齐缺字段）。"""
        from backend.services.unified_data_pool import MarketSnapshot as LegacySnap
        legacy = LegacySnap(symbol="ETH-PERP", price=3000, funding_rate=0.0001,
                            open_interest=1e6, timestamp=1700000000.0)
        snap = legacy_to_contract(legacy, venue="binance", bid=2999, ask=3001)
        assert snap.instrument.symbol == "ETH-PERP"
        assert snap.instrument.venue == "binance"
        assert snap.funding_rate == 0.0001
        assert snap.bid == 2999

    def test_contract_to_legacy(self, instrument):
        """新契约 → 旧 MarketSnapshot（兼容旧消费方）。"""
        snap = MarketSnapshot(
            ts_ns=1_700_000_000_000_000_000, instrument=instrument,
            bid=50000, ask=50001, mid=50000.5, last_trade=50000,
            last_trade_size=0.5, funding_rate=0.0001,
        )
        legacy = contract_to_legacy(snap)
        assert legacy.symbol == "BTC-PERP"
        assert legacy.funding_rate == 0.0001

    def test_make_instrument(self):
        inst = make_instrument("SOL-PERP", "bybit", adv_usd=5e8)
        assert inst.symbol == "SOL-PERP"
        assert inst.adv_usd == 5e8


class TestZeroBusinessDeps:
    """契约层不能 import 任何业务 service（可独立编译/迁移）。"""

    def test_no_service_imports(self):
        import inspect

        import backend.services.contracts.types as types_mod
        src = inspect.getsource(types_mod)
        # 不应 import 任何 backend.services 业务模块
        assert "from backend.services" not in src.replace(
            "from backend.services.contracts", ""  # 允许自引用
        )
        assert "import backend.api" not in src
