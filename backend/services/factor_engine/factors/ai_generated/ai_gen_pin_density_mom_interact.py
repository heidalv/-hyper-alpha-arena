"""AI因子: 插针密度与动量交互 | 置信:58% | 在高插针密度（影线主导、多空博弈激烈）环境中，短期动量更容易被反向修正。用影线密度作为环境权重，与短期收益方向交互，捕捉插针密集时的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """在高插针密度（影线主导、多空博弈激烈）环境中，短期动量更容易被反向修正。用影线密度作为环境权重，与短期收益方向交互，捕捉插针密集时的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mom_interact",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与动量交互",
            description="在高插针密度（影线主导、多空博弈激烈）环境中，短期动量更容易被反向修正。用影线密度作为环境权重，与短期收益方向交互，捕捉插针密集时的均值回归alpha。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        ret = data['close'].pct_change(3)
        result = (-ret * density).rolling(3).mean().clip(-1, 1)
        return result
