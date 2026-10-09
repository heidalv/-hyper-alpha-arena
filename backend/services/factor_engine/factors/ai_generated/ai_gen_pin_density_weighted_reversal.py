"""AI因子: 插针密度加权反转 | 置信:60% | 以(high-low)/body的20期均值作为插针密度环境分位，在插针密集环境下放大影线不对称反转信号，稀疏环境下衰减，构造环境自适应的插针反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """以(high-low)/body的20期均值作为插针密度环境分位，在插针密集环境下放大影线不对称反转信号，稀疏环境下衰减，构造环境自适应的插针反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_weighted_reversal",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="以(high-low)/body的20期均值作为插针密度环境分位，在插针密集环境下放大影线不对称反转信号，稀疏环境下衰减，构造环境自适应的插针反转因子。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        weight = (density / (density.rolling(20).mean() + 1e-9)).clip(0, 3)
        result = (asym.rolling(5).mean() * weight).clip(-1, 1)
        return result
