"""AI因子: 波动率稳定性因子 | 置信:60% | 衡量过去一段时间内平均真实波幅（ATR）的变异系数。当波动率不稳定（变异系数高）时，市场状态不确定，容易触发止损或反转，因子值为负；波动率稳定时为正。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityStabilityFactor(BaseFactor):
    """衡量过去一段时间内平均真实波幅（ATR）的变异系数。当波动率不稳定（变异系数高）时，市场状态不确定，容易触发止损或反转，因子值为负；波动率稳定时为正。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vola_stability",
            name="Volatility Stability Factor",
            display_name="波动率稳定性因子",
            description="衡量过去一段时间内平均真实波幅（ATR）的变异系数。当波动率不稳定（变异系数高）时，市场状态不确定，容易触发止损或反转，因子值为负；波动率稳定时为正。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算ATR
        high = data['high']
        low = data['low']
        close = data['close']
        tr = pd.concat([high - low,
                        (high - close.shift()).abs(),
                        (low - close.shift()).abs()], axis=1).max(axis=1)
        atr = tr.rolling(window=20).mean()
        # 计算ATR的变异系数（滚动标准差/滚动均值），窗口20
        atr_std = atr.rolling(window=20).std()
        atr_mean = atr.rolling(window=20).mean()
        cv = atr_std / atr_mean
        # 归一化到[-1,1]，使用分位数映射（反向）：高CV -> -1
        rank = cv.rank(pct=True)
        result = 1 - 2 * rank
        return result.fillna(0)
