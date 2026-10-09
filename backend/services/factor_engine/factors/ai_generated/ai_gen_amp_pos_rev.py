"""AI因子: 振幅位置反转 | 置信:55% | 收盘价在当日振幅区间中的相对位置反映多空力量对比。收盘位置过高(接近最高价)说明短期超买，过低说明超卖。用滚动窗口内的收盘位置均值构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class AmplitudePositionReversal(BaseFactor):
    """收盘价在当日振幅区间中的相对位置反映多空力量对比。收盘位置过高(接近最高价)说明短期超买，过低说明超卖。用滚动窗口内的收盘位置均值构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_amp_pos_rev",
            name="Amplitude Position Reversal",
            display_name="振幅位置反转",
            description="收盘价在当日振幅区间中的相对位置反映多空力量对比。收盘位置过高(接近最高价)说明短期超买，过低说明超卖。用滚动窗口内的收盘位置均值构造反转因子。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (-(pos.rolling(5).mean() - 0.5) * 2).clip(-1, 1)
        return result
