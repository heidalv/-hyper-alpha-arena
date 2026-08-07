"""
链上数据采集器（P2.8，方案 §P2.8 / §2.2.9）。

目标（诊断 P0 短板）：crypto 短线领先指标层目前几乎空缺。
5 类链上事件：
    1. netflow      交易所净流入（bearish）/流出（bullish）
    2. stablecoin   USDC/USDT 大额铸造（bullish 流动性领先）
    3. whale        巨鲸积累（流出冷钱包 bullish）
    4. mempool      pending-tx 抢跑/夹击预警（兼自我防护）
    5. dex_trades   DEX 大单（The Graph/Dune 批量历史）

数据源：CryptoQuant/Glassnode/Nansen/Arkham（netflow/whale）、
        Alchemy/QuickNode pending-tx（mempool）、The Graph/Dune（dex）。
reorg 安全：等确认数，避免重组致因子跳变。

生产环境接真实 API；当前提供事件类型 + 归一化 + reorg 防护框架。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class OnChainEventType(str, Enum):
    NETFLOW = "netflow"             # 交易所净流入
    STABLECOIN_MINT = "stablecoin_mint"
    WHALE_ACCUMULATION = "whale_accumulation"
    MEMPOOL_ALERT = "mempool_alert"
    DEX_LARGE_TRADE = "dex_large_trade"


class OnChainSignal(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class OnChainEvent:
    """链上事件。"""
    event_type: OnChainEventType
    asset: str                      # BTC/ETH/USDT...
    value_usd: float
    signal: OnChainSignal
    ts_ns: int = 0
    confirmations: int = 0          # 区块确认数（reorg 安全）
    source: str = ""                # cryptoquant/glassnode/alchemy/...
    detail: str = ""


# reorg 安全：最少确认数（不同链不同，ETH 主网 12 较安全）
MIN_CONFIRMATIONS = {"ethereum": 12, "bitcoin": 3, "arbitrum": 64}


class OnChainDataAgent:
    """
    链上数据采集 + 信号转换。

    生产：各 fetch_* 接真实 API（待 API key）。
    当前：提供事件归一化 + reorg 防护 + 信号方向判定框架。
    """

    def __init__(self, netflow_alert_threshold_usd: float = 5e7,
                 whale_threshold_usd: float = 1e6):
        self.netflow_threshold = netflow_alert_threshold_usd
        self.whale_threshold = whale_threshold_usd

    def classify_netflow_signal(self, netflow_usd: float) -> OnChainSignal:
        """
        交易所净流入信号方向。
        流入（正）= 潜在抛压 = bearish；流出（负）= 积累 = bullish。
        """
        if netflow_usd > self.netflow_threshold:
            return OnChainSignal.BEARISH
        if netflow_usd < -self.netflow_threshold:
            return OnChainSignal.BULLISH
        return OnChainSignal.NEUTRAL

    def classify_stablecoin_signal(self, mint_usd: float) -> OnChainSignal:
        """稳定币大额铸造 = 潜在买盘 = bullish。"""
        return OnChainSignal.BULLISH if mint_usd > self.netflow_threshold else OnChainSignal.NEUTRAL

    def is_whale_event(self, value_usd: float) -> bool:
        """是否达巨鲸阈值。"""
        return value_usd >= self.whale_threshold

    def is_reorg_safe(self, confirmations: int, chain: str = "ethereum") -> bool:
        """reorg 防护：确认数是否足够。"""
        return confirmations >= MIN_CONFIRMATIONS.get(chain, 12)

    def normalize_event(
        self, event_type: OnChainEventType, asset: str, value_usd: float,
        signal: OnChainSignal, ts_ns: int, confirmations: int = 0,
        source: str = "", detail: str = "",
    ) -> Optional[OnChainEvent]:
        """归一化原始链上数据为事件。reorg 不安全的事件返回 None。"""
        if not self.is_reorg_safe(confirmations):
            logger.debug(f"丢弃未确认事件 {event_type}（conf={confirmations}）")
            return None
        return OnChainEvent(
            event_type=event_type, asset=asset, value_usd=value_usd,
            signal=signal, ts_ns=ts_ns, confirmations=confirmations,
            source=source, detail=detail,
        )

    def aggregate_signals(self, events: list[OnChainEvent], asset: str) -> OnChainSignal:
        """聚合某资产的多个链上事件 → 综合信号。"""
        asset_events = [e for e in events if e.asset == asset]
        if not asset_events:
            return OnChainSignal.NEUTRAL
        score = sum(
            1 if e.signal == OnChainSignal.BULLISH else
            (-1 if e.signal == OnChainSignal.BEARISH else 0)
            for e in asset_events
        )
        if score > 0:
            return OnChainSignal.BULLISH
        if score < 0:
            return OnChainSignal.BEARISH
        return OnChainSignal.NEUTRAL
