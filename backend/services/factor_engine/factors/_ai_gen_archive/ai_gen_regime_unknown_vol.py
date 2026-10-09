"""AI因子: 未知行情波动率因子 | 置信:55% | 针对regime=unknown下的sl亏损，捕捉市场波动率异常放大且方向不明时的风险。用价格波动率与成交量波动率的背离程度，高值表示波动异常，应避免交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Regimeunknownvolatility(BaseFactor):
    """针对regime=unknown下的sl亏损，捕捉市场波动率异常放大且方向不明时的风险。用价格波动率与成交量波动率的背离程度，高值表示波动异常，应避免交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown_vol",
            name="RegimeUnknownVolatility",
            display_name="未知行情波动率因子",
            description="针对regime=unknown下的sl亏损，捕捉市场波动率异常放大且方向不明时的风险。用价格波动率与成交量波动率的背离程度，高值表示波动异常，应避免交易。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        volume = data['volume']
        price_vol = close.pct_change().rolling(10).std()
        volume_vol = volume.pct_change().rolling(10).std()
        result = (price_vol - volume_vol).clip(-1, 1)
        return result
