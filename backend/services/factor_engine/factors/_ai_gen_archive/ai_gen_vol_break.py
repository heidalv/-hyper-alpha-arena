"""AI因子: 波动率突破防守 | 置信:50% | 针对止损触发频繁的品种，识别波动率异常放大后的短期趋势衰竭。当价格突破近期高点但波动率骤增时，做反向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityBreakoutGuard(BaseFactor):
    """针对止损触发频繁的品种，识别波动率异常放大后的短期趋势衰竭。当价格突破近期高点但波动率骤增时，做反向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_break",
            name="Volatility_Breakout_Guard",
            display_name="波动率突破防守",
            description="针对止损触发频繁的品种，识别波动率异常放大后的短期趋势衰竭。当价格突破近期高点但波动率骤增时，做反向信号。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        range_20 = high_20 - low_20
        atr = (data['high'] - data['low']).rolling(14).mean()
        vol_ratio = atr / (range_20 + 1e-9)
        close_pos = (data['close'] - low_20) / (range_20 + 1e-9)
        result = (close_pos - 0.5) * (vol_ratio - 0.3).clip(0, 1) * 4
        return result.clip(-1, 1)
