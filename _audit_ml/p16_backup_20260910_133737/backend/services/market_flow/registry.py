"""
MarketFlowCollectorRegistry — 多交易所市场流采集器注册表（单例）

替代旧的 market_flow_collector 单例（单所硬绑 Hyperliquid）。
现在每个交易所一个 collector 实例，各自独立线程并行采集，
共享 market 表（按 exchange 字段隔离）。

用法（startup.py / main.py）：
    from services.market_flow import market_flow_registry, register_defaults
    register_defaults()                              # 注册 hyperliquid + asterdex
    market_flow_registry.start_all(symbols_map)      # {"hyperliquid": [...], "asterdex": [...]}
    ...
    market_flow_registry.stop_all()
"""

from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, List, Optional, Type

from services.market_flow.base_collector import BaseMarketFlowCollector

logger = logging.getLogger(__name__)


class MarketFlowCollectorRegistry:
    """
    采集器注册表单例。管理多个交易所的 collector 实例并行运行。
    """

    def __init__(self):
        self._classes: Dict[str, Type[BaseMarketFlowCollector]] = {}
        self._instances: Dict[str, BaseMarketFlowCollector] = {}
        self._lock = threading.Lock()
        self._defaults_registered = False

    # ── 注册 ──

    def register(
        self,
        exchange_id: str,
        collector_class: Type[BaseMarketFlowCollector],
    ) -> None:
        """注册一个交易所的 collector 类（启动时按需实例化）。"""
        with self._lock:
            self._classes[exchange_id] = collector_class
            logger.debug("[MarketFlowRegistry] 已注册 collector: %s", exchange_id)

    def get_registered_exchanges(self) -> List[str]:
        with self._lock:
            return list(self._classes.keys())

    def get(self, exchange_id: str) -> Optional[BaseMarketFlowCollector]:
        """获取已实例化的 collector（未启动则返回 None）。"""
        with self._lock:
            return self._instances.get(exchange_id)

    # ── 启动 / 停止 ──

    def start_all(
        self,
        symbols_map: Optional[Dict[str, List[str]]] = None,
        exchanges: Optional[List[str]] = None,
        aggregation_window_seconds: Optional[int] = None,
    ) -> Dict[str, bool]:
        """
        启动多个交易所的采集器。

        Args:
            symbols_map: {exchange_id: [symbols]}。某所未提供则用该所默认 symbol 加载逻辑。
            exchanges: 要启动的交易所列表。None 表示启动所有已注册的所。
            aggregation_window_seconds: 聚合窗口（秒），覆盖各 collector 默认值。

        Returns:
            {exchange_id: 是否启动成功}
        """
        symbols_map = symbols_map or {}
        target_exchanges = exchanges or list(self._classes.keys())
        results: Dict[str, bool] = {}

        for exchange_id in target_exchanges:
            if exchange_id not in self._classes:
                logger.warning("[MarketFlowRegistry] 未注册的交易所: %s，跳过", exchange_id)
                results[exchange_id] = False
                continue

            symbols = symbols_map.get(exchange_id)
            try:
                with self._lock:
                    collector = self._instances.get(exchange_id)
                    if collector is None:
                        cls = self._classes[exchange_id]
                        collector = cls(
                            aggregation_window_seconds=aggregation_window_seconds,
                        ) if aggregation_window_seconds is not None else cls()
                        self._instances[exchange_id] = collector

                ok = collector.start(symbols)
                results[exchange_id] = ok
                if not ok and symbols is None:
                    logger.info(
                        "[MarketFlowRegistry] %s 无 symbols 提供，等待会话订阅时再启动",
                        exchange_id,
                    )
            except Exception as e:
                logger.error(
                    "[MarketFlowRegistry] 启动 %s 失败: %s", exchange_id, e, exc_info=True,
                )
                results[exchange_id] = False

        return results

    def stop_all(self) -> None:
        """停止所有已启动的采集器。"""
        with self._lock:
            instances = list(self._instances.values())
        for collector in instances:
            try:
                collector.stop()
            except Exception as e:
                logger.error(
                    "[MarketFlowRegistry] 停止 %s 失败: %s",
                    collector.exchange_id, e, exc_info=True,
                )

    def stop(self, exchange_id: str) -> bool:
        """停止单个交易所的采集器。"""
        with self._lock:
            collector = self._instances.get(exchange_id)
        if collector is None:
            return False
        collector.stop()
        return True

    # ── 会话级订阅（引用计数）──

    def ensure_subscribed(
        self,
        exchange_id: str,
        symbols: List[str],
    ) -> bool:
        """
        确保某交易所的 collector 已启动并订阅了给定 symbols。
        会话启动时调用：若 collector 未运行则启动，已运行则合并 symbols。

        Returns:
            是否成功确保订阅
        """
        if exchange_id not in self._classes:
            logger.warning("[MarketFlowRegistry] ensure_subscribed: 未注册 %s", exchange_id)
            return False

        with self._lock:
            collector = self._instances.get(exchange_id)
            if collector is None:
                cls = self._classes[exchange_id]
                collector = cls()
                self._instances[exchange_id] = collector

        if not collector.running:
            return collector.start(symbols)
        else:
            # 已运行，合并新 symbols
            current = set(collector.subscribed_symbols)
            merged = list(dict.fromkeys(list(collector.subscribed_symbols) + [
                s for s in symbols if s not in current
            ]))
            if len(merged) != len(current):
                collector.refresh_subscriptions(merged)
            return True

    # ── 状态 ──

    def status_all(self) -> Dict[str, Dict]:
        with self._lock:
            ids = list(self._instances.keys())
        return {eid: self._instances[eid].get_status() for eid in ids}

    def get_active_exchanges(self) -> List[str]:
        """返回当前正在运行的交易所列表。"""
        with self._lock:
            return [
                eid for eid, c in self._instances.items() if c.running
            ]


# 单例
market_flow_registry = MarketFlowCollectorRegistry()


def register_defaults() -> None:
    """
    注册默认支持的采集器（幂等）：
    - hyperliquid: 原生 SDK WS
    - asterdex:   ccxt.pro watchTrades
    """
    if market_flow_registry._defaults_registered:
        return
    try:
        from services.market_flow.hyperliquid_collector import (
            HyperliquidMarketFlowCollector,
        )
        market_flow_registry.register("hyperliquid", HyperliquidMarketFlowCollector)
    except Exception as e:
        logger.warning("[MarketFlowRegistry] 注册 hyperliquid collector 失败: %s", e)

    try:
        from services.market_flow.asterdex_collector import (
            AsterdexMarketFlowCollector,
        )
        market_flow_registry.register("asterdex", AsterdexMarketFlowCollector)
    except Exception as e:
        logger.warning("[MarketFlowRegistry] 注册 asterdex collector 失败: %s", e)

    try:
        from services.market_flow.binance_collector import (
            BinanceMarketFlowCollector,
        )
        market_flow_registry.register("binance", BinanceMarketFlowCollector)
    except Exception as e:
        logger.warning("[MarketFlowRegistry] 注册 binance collector 失败: %s", e)

    market_flow_registry._defaults_registered = True
    logger.info(
        "[MarketFlowRegistry] 默认采集器已注册: %s",
        market_flow_registry.get_registered_exchanges(),
    )
