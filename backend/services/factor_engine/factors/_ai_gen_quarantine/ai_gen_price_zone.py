"""AI因子: 价格区域位置 | 置信:55% | 识别价格在近期波动区间内的相对位置。当价格处于区间中间时（50%附近），市场方向不明确（unknown regime），因子值偏负；当价格接近区间边界时，趋势较强，因子值偏正。使用过去N天的高低点，计算当前收盘价在区间中的百分比，并映射到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceZone(BaseFactor):
    """识别价格在近期波动区间内的相对位置。当价格处于区间中间时（50%附近），市场方向不明确（unknown regime），因子值偏负；当价格接近区间边界时，趋势较强，因子值偏正。使用过去N天的高低点，计算当前收盘价在区间中的百分比，并映射到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_price_zone",
            name="PriceZone",
            display_name="价格区域位置",
            description="识别价格在近期波动区间内的相对位置。当价格处于区间中间时（50%附近），市场方向不明确（unknown regime），因子值偏负；当价格接近区间边界时，趋势较强，因子值偏正。使用过去N天的高低点，计算当前收盘价在区间中的百分比，并映射到[-1,1]。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        high = data['high'].rolling(window=20, min_periods=1).max()
        low = data['low'].rolling(window=20, min_periods=1).min()
        range_ = high - low
        # 避免除以零
        range_ = range_.replace(0, 1e-10)
        position = (close - low) / range_
        # position 0~1，映射到[-1,1]：中间0.5附近为-1，两端为+1
        # 使用抛物线或线性：|position-0.5|*2 得到0~1，然后用1-这个值再映射？
        # 更直观：偏离中间越远评分越高
        deviation = (position - 0.5).abs() * 2  # 0~1
        # 当deviation=0（中间）时为-1，deviation=1（边界）时为+1
        result = deviation * 2 - 1
        return result.clip(-1, 1)
