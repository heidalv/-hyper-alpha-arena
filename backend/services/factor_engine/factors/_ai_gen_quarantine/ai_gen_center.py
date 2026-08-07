"""AI因子: 价格区间中心度 | 置信:60% | 识别价格处于近期波动区间中间位置的程度，中间位置往往对应震荡市（regime unknown）。计算当前收盘价在最近20周期高低点区间内的百分位，再映射到[-1,1]：接近0.5的位置映射为-1，靠近两端映射为+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Price_Center_in_Range(BaseFactor):
    """识别价格处于近期波动区间中间位置的程度，中间位置往往对应震荡市（regime unknown）。计算当前收盘价在最近20周期高低点区间内的百分位，再映射到[-1,1]：接近0.5的位置映射为-1，靠近两端映射为+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_center",
            name="Price Center in Range",
            display_name="价格区间中心度",
            description="识别价格处于近期波动区间中间位置的程度，中间位置往往对应震荡市（regime unknown）。计算当前收盘价在最近20周期高低点区间内的百分位，再映射到[-1,1]：接近0.5的位置映射为-1，靠近两端映射为+1。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high'].rolling(20).max()
        low = data['low'].rolling(20).min()
        range_ = high - low
        # 避免除以0
        range_ = range_.replace(0, np.nan)
        pct = (close - low) / range_
        pct = pct.fillna(0.5)
        # 将0.5映射到-1，0和1映射到+1
        # 使用公式：1 - 2 * |pct - 0.5|
        result = 1 - 2 * (pct - 0.5).abs()
        return result
