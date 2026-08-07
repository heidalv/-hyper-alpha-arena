"""
P3.3 回测 parity + P3.2b DEX/MEV + P3.2c FIX 测试。

P3.3 完成标准：BacktestExecutionClient 同核；parity 校验量化偏差。
P3.2b 完成标准：DEX 连接器接口 + MEV 防护逻辑。
P3.2c 完成标准：FIX 骨架 + 序列号 gap 检测。
"""
from __future__ import annotations

import time

import pytest

from backend.services.contracts.types import ApprovedTarget, Instrument, OrderStatus
from backend.services.exchange.dex import (
    DriftConnector,
    FlashbotsConfig,
    MEVProtector,
    get_dex_connector,
)
from backend.services.exchange.fix import FIXClient, FIXConfig, is_fix_enabled
from backend.services.execution.backtest_client import (
    BacktestExecutionClient,
    FillModel,
    run_parity_check,
)
from backend.services.execution.client import ExecutionClient, _make_order_event

pytestmark = pytest.mark.unit


def _inst(sym="BTC-PERP"):
    return Instrument(symbol=sym, venue="binance", kind="perp")


def _approved(qty=1.0):
    return ApprovedTarget(ts_ns=time.time_ns(), instrument=_inst(), approved_qty=qty)


# ==================== P3.3 回测 parity ====================

class TestFillModel:
    def test_basic_slippage(self):
        fm = FillModel(slippage_bps=2.0)
        price, qty, status = fm.simulate_fill(1.0, 50000, "buy")
        # buy 滑点正方向
        assert price > 50000
        assert status == OrderStatus.FILLED

    def test_market_impact(self):
        """大单浅盘 → 更大滑点。"""
        fm = FillModel(slippage_bps=0)
        _, small_price, _ = fm.simulate_fill(0.1, 50000, "buy", book_depth_usd=1e7)
        _, large_price, _ = fm.simulate_fill(100, 50000, "buy", book_depth_usd=1e7)
        assert large_price > small_price  # 大单冲击更大

    def test_partial_fill(self):
        fm = FillModel(partial_fill_prob=1.0)
        _, qty, status = fm.simulate_fill(10.0, 50000, "buy", book_depth_usd=1000)
        # 深度只够 1000/50000 = 0.02
        assert qty < 10.0
        assert status == OrderStatus.PARTIAL


class TestBacktestClient:
    def test_execute_with_oracle(self):
        def oracle(ts, sym):
            return 50000.0, 1e7
        bt = BacktestExecutionClient(price_oracle=oracle)
        evt = bt.execute(_approved(1.0))
        assert evt.status == OrderStatus.FILLED
        assert evt.fill_price > 0

    def test_execute_no_oracle_rejected(self):
        bt = BacktestExecutionClient(price_oracle=None)
        evt = bt.execute(_approved())
        assert evt.status == OrderStatus.REJECTED


class TestParityCheck:
    def test_parity_quantifies_deviation(self):
        """parity 校验量化 live vs backtest 偏差。"""

        class LiveStub(ExecutionClient):
            def execute(self, target):
                return _make_order_event(
                    target, OrderStatus.FILLED, 50005,
                    abs(target.approved_qty), 0.1,
                    client_id="live",
                )

        def oracle(ts, sym):
            return 50000.0, 1e9
        bt = BacktestExecutionClient(
            price_oracle=oracle,
            fill_model=FillModel(slippage_bps=0),
        )
        targets = [_approved(1.0) for _ in range(5)]
        result = run_parity_check(targets, LiveStub(), bt,
                                  max_price_dev_bps=50, max_fill_qty_diff_pct=0.2)
        assert result["n_compared"] == 5
        assert "parity_ok" in result
        assert result["max_price_dev_bps"] > 0


# ==================== P3.2b DEX / MEV ====================

class TestDEXConnectors:
    def test_drift_registered(self):
        assert get_dex_connector("drift") is not None

    def test_gmx_registered(self):
        assert get_dex_connector("gmx") is not None

    def test_drift_placeholder(self):
        c = DriftConnector()
        with pytest.raises(NotImplementedError):
            c.place_order("BTC-PERP", "buy", 1.0)


class TestMEVProtector:
    def test_large_tx_protected(self):
        mp = MEVProtector()
        assert mp.should_protect(5e4) is True  # 大额强制 Protect

    def test_small_tx_skipped(self):
        mp = MEVProtector()
        assert mp.should_protect(100) is False

    def test_protected_rpc_url(self):
        mp = MEVProtector()
        assert "flashbots" in mp.protected_rpc()

    def test_disabled(self):
        mp = MEVProtector(FlashbotsConfig(enabled=False))
        assert mp.should_protect(1e6) is False


# ==================== P3.2c FIX ====================

class TestFIXClient:
    def test_disabled_by_default(self):
        assert is_fix_enabled() is False

    def test_not_connected_rejects(self):
        client = FIXClient(FIXConfig(enabled=True))
        # 未 connect
        result = client.send_order("BTC-PERP", "buy", 1.0)
        assert result["status"] == "REJECTED"

    def test_seq_gap_detection(self):
        client = FIXClient(FIXConfig(enabled=True))
        client.connect()
        assert client.check_seq_gap(1) is False  # 正常
        assert client.check_seq_gap(5) is True   # 跳到 5，缺口

    def test_connect_requires_enabled(self):
        client = FIXClient(FIXConfig(enabled=False))
        assert client.connect() is False
