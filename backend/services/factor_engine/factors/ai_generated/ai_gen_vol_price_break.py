"""AI因子: 量价突破确认 | 置信:58% | 收盘位置结合成交量放大程度，放量且收于高位时确认突破，缩量高位则衰减，输出方向性强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceBreakoutConfirm(BaseFactor):
    """收盘位置结合成交量放大程度，放量且收于高位时确认突破，缩量高位则衰减，输出方向性强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_break",
            name="Volume Price Breakout Confirm",
            display_name="量价突破确认",
            description="收盘位置结合成交量放大程度，放量且收于高位时确认突破，缩量高位则衰减，输出方向性强度。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = ((data['close'] - data['low']) / rng) * 2 - 1
        vratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (pos * (vratio - 1)).rolling(5).mean().clip(-1, 1)
        return result
