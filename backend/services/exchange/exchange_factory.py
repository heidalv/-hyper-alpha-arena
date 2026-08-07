"""
ExchangeClientFactory — 交易所客户端工厂

工厂模式按配置选择交易所适配器实例。
"""

from typing import Dict, Type

from backend.services.exchange.base_exchange_client import BaseExchangeClient


class ExchangeClientFactory:
    """
    交易所客户端工厂

    通过注册机制支持动态添加交易所适配器。
    """

    _registry: Dict[str, Type[BaseExchangeClient]] = {}

    @classmethod
    def register(cls, exchange_type: str, client_class: Type[BaseExchangeClient]):
        """注册交易所适配器"""
        cls._registry[exchange_type] = client_class

    @classmethod
    def create(cls, exchange_type: str, **kwargs) -> BaseExchangeClient:
        """创建交易所客户端实例"""
        if exchange_type not in cls._registry:
            raise ValueError(
                f"Unknown exchange: {exchange_type}. "
                f"Available: {list(cls._registry.keys())}"
            )
        return cls._registry[exchange_type](**kwargs)

    @classmethod
    def get_registered_exchanges(cls) -> list:
        """获取已注册的交易所列表"""
        return list(cls._registry.keys())

    @classmethod
    def is_registered(cls, exchange_type: str) -> bool:
        """检查交易所是否已注册"""
        return exchange_type in cls._registry

    @classmethod
    def clear_registry(cls):
        """清空注册表（主要用于测试）"""
        cls._registry = {}


def _register_defaults():
    from backend.services.exchange.hyperliquid_adapter import HyperliquidAdapter
    from backend.services.exchange.binance_adapter import BinanceAdapter
    from backend.services.exchange.bybit_adapter import BybitAdapter
    from backend.services.exchange.okx_adapter import OKXAdapter
    from backend.services.exchange.gateio_adapter import GateioAdapter
    from backend.services.exchange.asterdex_adapter import AsterdexAdapter

    ExchangeClientFactory.register("hyperliquid", HyperliquidAdapter)
    ExchangeClientFactory.register("binance", BinanceAdapter)
    ExchangeClientFactory.register("bybit", BybitAdapter)
    ExchangeClientFactory.register("okx", OKXAdapter)
    ExchangeClientFactory.register("gateio", GateioAdapter)
    ExchangeClientFactory.register("asterdex", AsterdexAdapter)


_register_defaults()
