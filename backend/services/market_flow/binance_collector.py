"""
BinanceMarketFlowCollector — Binance CVD/市场流采集器（ccxt.pro binance 原生驱动）。

复用 AsterdexMarketFlowCollector 的全部 REST 轮询骨架（trades/orderbook/asset_metrics），
仅覆盖 exchange_id 与 ccxt 实例构建：使用 ccxt binance 默认 fapi 端点（不覆盖 URL）。

说明：
- 默认所从 asterdex 切到 binance 后，市场流（CVD/盘口/OI）需跟随切换，
  否则信号层仍消费 asterdex 订单流，与成交所（binance）脱节。
- 不与 Asterdex 共享全局限流（_wait_global_ban 置空）；binance 独立限流足够宽松。
"""

from __future__ import annotations

import os
from typing import Any

from services.market_flow.asterdex_collector import AsterdexMarketFlowCollector


class BinanceMarketFlowCollector(AsterdexMarketFlowCollector):
    """Binance CVD 采集器（REST 轮询，默认 binance fapi 端点）。"""

    @property
    def exchange_id(self) -> str:
        return "binance"

    def _create_ccxt_exchange(self) -> Any:
        import ccxt.async_support as ccxt

        ex = ccxt.binance({
            "enableRateLimit": True,
            "options": {
                "defaultType": "future",
                "fetchMarkets": {"types": ["linear"]},
            },
            "timeout": 15000,
            "apiKey": "",
            "secret": "",
        })
        _proxy = os.environ.get("BINANCE_HTTPS_PROXY") or os.environ.get("HTTPS_PROXY")
        if _proxy:
            ex.proxies = {"http": _proxy, "https": _proxy}
            ex.aiohttp_proxy = _proxy
        return ex

    async def _wait_global_ban(self) -> None:
        """Binance 不与 Asterdex 共享全局限流；无操作。"""
        return
