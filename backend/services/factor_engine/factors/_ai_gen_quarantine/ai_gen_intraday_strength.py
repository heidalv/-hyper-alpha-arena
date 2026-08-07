"""AI因子: 日内多头衰竭因子 | 置信:70% | 基于每日收盘价在日内区间的位置，若连续多日收盘价靠近日内低点，说明多头无力推高，做多容易触发止损。计算(close-low)/(high-low)的5日均值，数值低时输出负信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Intraday_Bullish_Exhaustion(BaseFactor):
    """基于每日收盘价在日内区间的位置，若连续多日收盘价靠近日内低点，说明多头无力推高，做多容易触发止损。计算(close-low)/(high-low)的5日均值，数值低时输出负信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_intraday_strength",
            name="Intraday Bullish Exhaustion",
            display_name="日内多头衰竭因子",
            description="基于每日收盘价在日内区间的位置，若连续多日收盘价靠近日内低点，说明多头无力推高，做多容易触发止损。计算(close-low)/(high-low)的5日均值，数值低时输出负信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        high = data['high']
        low = data['low']
        close = data['close']
        # 防止除零
        denom = high - low
        denom = denom.replace(0, np.nan)
        # 日内位置
        position = (close - low) / denom
        # 5日均值
        avg_pos = position.rolling(5).mean()
        # 映射到[-1,1]：低于0.4为负向，高于0.6为正向
        result = np.where(avg_pos < 0.4, -1.0, np.where(avg_pos > 0.6, 1.0, 0.0))
        return result
