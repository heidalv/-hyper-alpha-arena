"""AI因子: 上影线比率 | 置信:60% | 计算每日上影线长度相对于真实波幅的比率，并取3周期平均。长上影线表示上方抛压，该比率高时因子为负，提示做多风险。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Upper_Shadow_Ratio(BaseFactor):
    """计算每日上影线长度相对于真实波幅的比率，并取3周期平均。长上影线表示上方抛压，该比率高时因子为负，提示做多风险。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_shadow",
            name="Upper Shadow Ratio",
            display_name="上影线比率",
            description="计算每日上影线长度相对于真实波幅的比率，并取3周期平均。长上影线表示上方抛压，该比率高时因子为负，提示做多风险。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        open_ = data['open']
        close = data['close']
        upper_shadow = high - np.maximum(open_, close)
        true_range = np.maximum(high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1)))
        ratio = upper_shadow / true_range
        ratio = ratio.fillna(0)
        factor = -ratio.rolling(3).mean()
        return factor.clip(-1, 1)
