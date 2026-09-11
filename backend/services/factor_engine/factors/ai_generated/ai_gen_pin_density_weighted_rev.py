"""AI因子: 插针密度加权反转 | 置信:58% | 在高插针密度（影线相对实体显著）的波动环境下，影线不对称度所蕴含的均值回归信号更强。以插针密度作为环境权重，与不对称度滚动均值相乘，放大高波动插针环境下的反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """在高插针密度（影线相对实体显著）的波动环境下，影线不对称度所蕴含的均值回归信号更强。以插针密度作为环境权重，与不对称度滚动均值相乘，放大高波动插针环境下的反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_weighted_rev",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="在高插针密度（影线相对实体显著）的波动环境下，影线不对称度所蕴含的均值回归信号更强。以插针密度作为环境权重，与不对称度滚动均值相乘，放大高波动插针环境下的反转alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        result = (asym.rolling(3).mean() * density).clip(-1, 1)
        return result
