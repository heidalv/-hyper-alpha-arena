"""AI因子: 空头压力因子 | 置信:60% | 日内收盘价位于K线低点附近（relative_weakness低），且持续多日，表明空头占优，做多风险高。计算过去10日每日(close - low)/(high - low)的均值，低于阈值输出负信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Bearish_Pressure(BaseFactor):
    """日内收盘价位于K线低点附近（relative_weakness低），且持续多日，表明空头占优，做多风险高。计算过去10日每日(close - low)/(high - low)的均值，低于阈值输出负信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_bearish_pressure",
            name="Bearish Pressure",
            display_name="空头压力因子",
            description="日内收盘价位于K线低点附近（relative_weakness低），且持续多日，表明空头占优，做多风险高。计算过去10日每日(close - low)/(high - low)的均值，低于阈值输出负信号。",
            category="behavioral",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd, numpy as np
        # 避免除零
        range_ = data['high'] - data['low']
        range_ = range_.replace(0, np.nan)
        relative_weakness = (data['close'] - data['low']) / range_
        avg_weakness = relative_weakness.rolling(10).mean()
        # 当均值<0.4时，空头压力大，信号负值
        result = -np.clip((0.4 - avg_weakness) / 0.2, 0, 1)
        result = result.fillna(0).clip(-1,1)
        return result
