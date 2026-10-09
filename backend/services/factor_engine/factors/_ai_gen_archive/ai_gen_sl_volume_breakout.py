"""AI因子: 止损量能突破因子 | 置信:50% | 针对止损单亏损模式，结合成交量放大和价格突破。当成交量显著高于近期中位数且价格突破近期高低点时，产生反向信号，避免在量能突破时追单。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class StopLossVolumeBreakout(BaseFactor):
    """针对止损单亏损模式，结合成交量放大和价格突破。当成交量显著高于近期中位数且价格突破近期高低点时，产生反向信号，避免在量能突破时追单。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_volume_breakout",
            name="Stop_Loss_Volume_Breakout",
            display_name="止损量能突破因子",
            description="针对止损单亏损模式，结合成交量放大和价格突破。当成交量显著高于近期中位数且价格突破近期高低点时，产生反向信号，避免在量能突破时追单。",
            category="composite",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        vol_ratio = data['volume'] / (data['volume'].rolling(20).median() + 1e-9)
        high_break = (data['close'] > data['high'].rolling(10).max().shift(1)).astype(float)
        low_break = (data['close'] < data['low'].rolling(10).min().shift(1)).astype(float)
        result = ((vol_ratio - 1) * (high_break - low_break)).clip(-1, 1)
        return result
