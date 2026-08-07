"""
Market Flow 多交易所采集子系统

架构（替代旧的 services/market_flow_collector.py 单所硬绑实现）：

    ExchangeTrade (base_exchange_client.ExchangeTrade)
            │
            ▼
    BaseMarketFlowCollector (base_collector.py)   ← 抽象基类 + 可复用聚合/flush/replay
        ├── HyperliquidMarketFlowCollector        ← 原生 SDK WS（从旧 MarketFlowCollector 抽取）
        └── AsterdexMarketFlowCollector           ← ccxt.pro watchTrades
            │
            ▼
    MarketFlowCollectorRegistry (registry.py)     ← 单例，多实例并行
            │
            ▼
    DB: crypto market tables (MarketTradesAggregated / MarketOrderbookSnapshots / MarketAssetMetrics)
       按 exchange 字段隔离不同交易所的数据

启动入口：startup.py / main.py → registry.start_all(symbols_map)
"""

from services.market_flow.registry import (
    MarketFlowCollectorRegistry,
    market_flow_registry,
    register_defaults,
)

__all__ = [
    "MarketFlowCollectorRegistry",
    "market_flow_registry",
    "register_defaults",
]
