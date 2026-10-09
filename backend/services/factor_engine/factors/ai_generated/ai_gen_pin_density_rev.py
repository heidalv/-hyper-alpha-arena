"""AI因子: 插针密度加权反转 | 置信:58% | 在高插针密度（影线主导、波动环境剧烈）时，短期收益方向更容易被反向修正。将影线不对称度与短期收益方向交互，再乘以插针密度分位，捕捉插针环境下的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """在高插针密度（影线主导、波动环境剧烈）时，短期收益方向更容易被反向修正。将影线不对称度与短期收益方向交互，再乘以插针密度分位，捕捉插针环境下的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_rev",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="在高插针密度（影线主导、波动环境剧烈）时，短期收益方向更容易被反向修正。将影线不对称度与短期收益方向交互，再乘以插针密度分位，捕捉插针环境下的均值回归alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        ret = data['close'].pct_change(3)
        result = (asym.rolling(3).mean() * (-ret) * density).clip(-1, 1)
        return result
