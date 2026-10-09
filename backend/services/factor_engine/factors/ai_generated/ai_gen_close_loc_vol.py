"""AI因子: 收盘位置波动调整 | 置信:55% | 收盘价在当日高低区间中的相对位置反映买方力量强弱，结合已实现波动率归一化，位置高且波动低时上涨概率更高，输出[-1,1]方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class CloseLocationVolatilityAdjusted(BaseFactor):
    """收盘价在当日高低区间中的相对位置反映买方力量强弱，结合已实现波动率归一化，位置高且波动低时上涨概率更高，输出[-1,1]方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_loc_vol",
            name="Close Location Volatility Adjusted",
            display_name="收盘位置波动调整",
            description="收盘价在当日高低区间中的相对位置反映买方力量强弱，结合已实现波动率归一化，位置高且波动低时上涨概率更高，输出[-1,1]方向信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = data['high'] - data['low'] + 1e-9
        loc = (data['close'] - data['low']) / rng
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((loc - 0.5) / vol).rolling(3).mean().clip(-1, 1)
        return result
