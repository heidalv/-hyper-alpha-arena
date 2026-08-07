"""AI因子: 价格中枢偏离因子 | 置信:55% | 衡量当前价格在近期最高最低点区间内的相对位置，当价格接近区间中点（0.4-0.6）时，处于震荡中枢，趋势不明确，做多易亏损。因子负值表示中枢附近风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Mid_Price_Proximity(BaseFactor):
    """衡量当前价格在近期最高最低点区间内的相对位置，当价格接近区间中点（0.4-0.6）时，处于震荡中枢，趋势不明确，做多易亏损。因子负值表示中枢附近风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_midprice",
            name="Mid-Price Proximity",
            display_name="价格中枢偏离因子",
            description="衡量当前价格在近期最高最低点区间内的相对位置，当价格接近区间中点（0.4-0.6）时，处于震荡中枢，趋势不明确，做多易亏损。因子负值表示中枢附近风险。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        period = 20
        high_max = data['high'].rolling(period).max()
        low_min = data['low'].rolling(period).min()
        range_ = high_max - low_min
        # 避免除零
        range_ = range_.replace(0, np.nan)
        pos = (data['close'] - low_min) / range_
        # pos在0-1之间，越接近0.5，风险越大
        # 用高斯核或简单绝对值
        risk = np.abs(pos - 0.5) * 2  # 0到1之间，0.5时risk=0，边界时risk=1
        # 映射到[-1,1]，低risk（靠近边沿）为正，高risk（中间）为负
        result = 1 - risk * 2  # 0->1, 0.5->0, 1->-1
        result = result.rolling(3).mean()
        return result.fillna(0)
