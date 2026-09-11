"""AI因子: 插针密度与动量交互 | 置信:58% | 插针密度(影线/实体比值的rolling均值)刻画市场情绪化程度，在高插针密度环境下短期动量更易反转。将插针不对称度rolling均值与短期收益方向相乘，构造可测IC的反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """插针密度(影线/实体比值的rolling均值)刻画市场情绪化程度，在高插针密度环境下短期动量更易反转。将插针不对称度rolling均值与短期收益方向相乘，构造可测IC的反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_momentum",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与动量交互",
            description="插针密度(影线/实体比值的rolling均值)刻画市场情绪化程度，在高插针密度环境下短期动量更易反转。将插针不对称度rolling均值与短期收益方向相乘，构造可测IC的反转因子。",
            category="composite",
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
        ret = data['close'].pct_change(5)
        result = (asym.rolling(3).mean() * (density / (density.rolling(50).mean() + 1e-9)) * (-ret).clip(-1, 1)).clip(-1, 1)
        return result
