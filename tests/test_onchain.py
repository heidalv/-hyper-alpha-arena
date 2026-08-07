"""
P2.8 OnChainDataAgent 测试。

完成标准（方案 P2.8）：5 类事件采集 + reorg 安全 + 信号方向判定。
"""
from __future__ import annotations

import pytest

from backend.services.data.onchain_collector import (
    OnChainDataAgent,
    OnChainEvent,
    OnChainEventType,
    OnChainSignal,
)

pytestmark = pytest.mark.unit


class TestOnChainSignals:
    def test_netflow_inflow_bearish(self):
        agent = OnChainDataAgent(netflow_alert_threshold_usd=5e7)
        assert agent.classify_netflow_signal(1e8) == OnChainSignal.BEARISH  # 流入=抛压

    def test_netflow_outflow_bullish(self):
        agent = OnChainDataAgent(netflow_alert_threshold_usd=5e7)
        assert agent.classify_netflow_signal(-1e8) == OnChainSignal.BULLISH  # 流出=积累

    def test_netflow_neutral(self):
        agent = OnChainDataAgent(netflow_alert_threshold_usd=5e7)
        assert agent.classify_netflow_signal(1e6) == OnChainSignal.NEUTRAL

    def test_stablecoin_mint_bullish(self):
        agent = OnChainDataAgent()
        assert agent.classify_stablecoin_signal(2e8) == OnChainSignal.BULLISH


class TestWhaleAndReorg:
    def test_whale_threshold(self):
        agent = OnChainDataAgent(whale_threshold_usd=1e6)
        assert agent.is_whale_event(5e6)
        assert not agent.is_whale_event(5e5)

    def test_reorg_safe_ethereum(self):
        agent = OnChainDataAgent()
        assert agent.is_reorg_safe(12, "ethereum")
        assert not agent.is_reorg_safe(5, "ethereum")

    def test_reorg_safe_bitcoin(self):
        agent = OnChainDataAgent()
        assert agent.is_reorg_safe(3, "bitcoin")
        assert not agent.is_reorg_safe(1, "bitcoin")


class TestEventNormalization:
    def test_normalize_drops_unconfirmed(self):
        """reorg 不安全的事件被丢弃。"""
        agent = OnChainDataAgent()
        evt = agent.normalize_event(
            OnChainEventType.NETFLOW, "BTC", 1e8, OnChainSignal.BEARISH,
            ts_ns=1000, confirmations=2,  # ETH 需 12
        )
        assert evt is None

    def test_normalize_confirmed(self):
        agent = OnChainDataAgent()
        evt = agent.normalize_event(
            OnChainEventType.NETFLOW, "BTC", -1e8, OnChainSignal.BULLISH,
            ts_ns=1000, confirmations=15, source="glassnode",
        )
        assert evt is not None
        assert evt.signal == OnChainSignal.BULLISH


class TestAggregation:
    def test_aggregate_bullish(self):
        agent = OnChainDataAgent()
        events = [
            OnChainEvent(OnChainEventType.NETFLOW, "BTC", -1e8, OnChainSignal.BULLISH, 1000, 15),
            OnChainEvent(OnChainEventType.STABLECOIN_MINT, "USDT", 2e8, OnChainSignal.BULLISH, 1100, 15),
            OnChainEvent(OnChainEventType.WHALE_ACCUMULATION, "BTC", 5e6, OnChainSignal.BULLISH, 1200, 15),
        ]
        assert agent.aggregate_signals(events, "BTC") == OnChainSignal.BULLISH

    def test_aggregate_empty(self):
        agent = OnChainDataAgent()
        assert agent.aggregate_signals([], "BTC") == OnChainSignal.NEUTRAL

    def test_five_event_types(self):
        """≥5 类链上事件（netflow/stablecoin/whale/mempool/dex）。"""
        assert len(list(OnChainEventType)) >= 5
