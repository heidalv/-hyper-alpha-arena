"""AI因子: 振幅位置反转 | 置信:55% | 收盘价在当日高低区间中的相对位置反映多空力量：收盘接近高点(位置高)短期超买易回落，收盘接近低点(位置低)超卖易反弹。用区间位置偏离中值的程度构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AmplitudePositionReversal(BaseFactor):
    """收盘价在当日高低区间中的相对位置反映多空力量：收盘接近高点(位置高)短期超买易回落，收盘接近低点(位置低)超卖易反弹。用区间位置偏离中值的程度构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_amp_position_rev",
            name="Amplitude Position Reversal",
            display_name="振幅位置反转",
            description="收盘价在当日高低区间中的相对位置反映多空力量：收盘接近高点(位置高)短期超买易回落，收盘接近低点(位置低)超卖易反弹。用区间位置偏离中值的程度构造反转因子。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = data['high'] - data['low'] + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (-(pos - 0.5) * 2).rolling(3).mean().clip(-1, 1)
        return result
