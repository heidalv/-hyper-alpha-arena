"""AI因子: 假突破风险 | 置信:60% | 价格处于近期高低点极端位置时，结合成交量爆发程度，正值为有效突破，负值为缩量假突破风险。用于识别止损触发前常见的假突破模式。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class FakeBreakoutRisk(BaseFactor):
    """价格处于近期高低点极端位置时，结合成交量爆发程度，正值为有效突破，负值为缩量假突破风险。用于识别止损触发前常见的假突破模式。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_fb",
            name="Fake Breakout Risk",
            display_name="假突破风险",
            description="价格处于近期高低点极端位置时，结合成交量爆发程度，正值为有效突破，负值为缩量假突破风险。用于识别止损触发前常见的假突破模式。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high'].rolling(20).max()
        low = data['low'].rolling(20).min()
        close = data['close']
        price_pos = (close - low) / (high - low + 1e-9)
        extreme = (2 * (price_pos - 0.5)).abs()
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        vol_factor = (vol_ratio - 1).clip(-1, 1)
        result = extreme * vol_factor
        return result.fillna(0)
