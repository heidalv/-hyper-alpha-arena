"""AI因子: 波动率收缩盘整 | 置信:60% | 识别价格在狭窄范围内波动且成交量萎缩的盘整状态，这种状态下容易触发止损和超时亏损。使用布林带宽度比例和成交量相对均值，当布林带宽低于20日均值且成交量低于20日均值时，因子值为负；否则为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Consolidation_Indicator(BaseFactor):
    """识别价格在狭窄范围内波动且成交量萎缩的盘整状态，这种状态下容易触发止损和超时亏损。使用布林带宽度比例和成交量相对均值，当布林带宽低于20日均值且成交量低于20日均值时，因子值为负；否则为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_consolidation",
            name="Volatility Consolidation Indicator",
            display_name="波动率收缩盘整",
            description="识别价格在狭窄范围内波动且成交量萎缩的盘整状态，这种状态下容易触发止损和超时亏损。使用布林带宽度比例和成交量相对均值，当布林带宽低于20日均值且成交量低于20日均值时，因子值为负；否则为正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        high = data['high']
        low = data['low']
        volume = data['volume']
        # Bollinger Bands width
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        bb_width = 2 * std20 / sma20
        bb_width_ma = bb_width.rolling(20).mean()
        # Volume
        vol_ma20 = volume.rolling(20).mean()
        # Consolidation condition
        cond1 = bb_width < bb_width_ma
        cond2 = volume < vol_ma20
        # Combine
        raw = (cond1.astype(float) + cond2.astype(float)) / 2
        # Scale to [-1,1]
        result = -1 + 2 * raw
        return result
