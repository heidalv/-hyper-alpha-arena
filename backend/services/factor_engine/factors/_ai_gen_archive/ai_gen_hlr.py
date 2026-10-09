"""AI因子: 高低点反转压力 | 置信:60% | 利用日内振幅与收盘位置判断多空失衡。当收盘接近区间低点且振幅扩大时，空头压力强，反之亦然，适用于止损频繁的品种。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class HighLowReversalPressure(BaseFactor):
    """利用日内振幅与收盘位置判断多空失衡。当收盘接近区间低点且振幅扩大时，空头压力强，反之亦然，适用于止损频繁的品种。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_hlr",
            name="High-Low Reversal Pressure",
            display_name="高低点反转压力",
            description="利用日内振幅与收盘位置判断多空失衡。当收盘接近区间低点且振幅扩大时，空头压力强，反之亦然，适用于止损频繁的品种。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = data['high'] - data['low']
        pos = (data['close'] - data['low']) / (rng + 1e-9)
        amp = rng / (data['close'] + 1e-9)
        result = ((pos - 0.5) * amp).clip(-1, 1)
        return result
