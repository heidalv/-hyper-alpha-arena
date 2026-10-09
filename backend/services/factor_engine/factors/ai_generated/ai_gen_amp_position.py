"""AI因子: 振幅位置反转 | 置信:60% | 收盘价在近期高低区间中的相对位置。当收盘价接近区间高点时超买，未来回落概率高；接近低点时超卖，未来反弹概率高。用位置偏离中值的程度构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AmplitudePositionReversal(BaseFactor):
    """收盘价在近期高低区间中的相对位置。当收盘价接近区间高点时超买，未来回落概率高；接近低点时超卖，未来反弹概率高。用位置偏离中值的程度构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_amp_position",
            name="Amplitude Position Reversal",
            display_name="振幅位置反转",
            description="收盘价在近期高低区间中的相对位置。当收盘价接近区间高点时超买，未来回落概率高；接近低点时超卖，未来反弹概率高。用位置偏离中值的程度构造反转因子。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_ma = data['high'].rolling(20).max()
        low_ma = data['low'].rolling(20).min()
        pos = (data['close'] - low_ma) / (high_ma - low_ma + 1e-9)
        result = (0.5 - pos).clip(-1, 1)
        return result
