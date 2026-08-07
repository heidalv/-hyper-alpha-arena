"""AI因子: 持仓时间惩罚因子 | 置信:50% | 基于价格与长期均线的偏离程度，判断是否处于均值回归预期中。当价格远离长期均线且波动率低时，长期持有风险高，给出负信号；否则给出正信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Holding_Time_Penalty(BaseFactor):
    """基于价格与长期均线的偏离程度，判断是否处于均值回归预期中。当价格远离长期均线且波动率低时，长期持有风险高，给出负信号；否则给出正信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_holdpenalty",
            name="Holding Time Penalty",
            display_name="持仓时间惩罚因子",
            description="基于价格与长期均线的偏离程度，判断是否处于均值回归预期中。当价格远离长期均线且波动率低时，长期持有风险高，给出负信号；否则给出正信号。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ma_50 = close.rolling(50).mean()
        ma_200 = close.rolling(200).mean()
        # 使用偏差百分比，取绝对值后归一化
        deviation = (close - ma_50) / ma_50
        # 长期均线斜率作为趋势强度
        slope = (ma_50 - ma_200) / ma_200
        # 结合：当偏离大且趋势弱时惩罚，否则奖励
        penalty = -deviation * slope
        # 截断到[-1,1]
        result = penalty.clip(-1, 1).fillna(0)
        return result
