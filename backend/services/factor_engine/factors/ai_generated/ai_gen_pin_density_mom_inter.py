"""AI因子: 插针密度与动量交互 | 置信:58% | 以影线/实体比值的20期均值刻画插针密度环境，与短期收益方向交互。高插针密度下短期反转更强，低密度下动量延续，从而捕捉波动环境切换的 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """以影线/实体比值的20期均值刻画插针密度环境，与短期收益方向交互。高插针密度下短期反转更强，低密度下动量延续，从而捕捉波动环境切换的 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mom_inter",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与动量交互",
            description="以影线/实体比值的20期均值刻画插针密度环境，与短期收益方向交互。高插针密度下短期反转更强，低密度下动量延续，从而捕捉波动环境切换的 alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        ret = data['close'].pct_change(3)
        result = (-ret * density).rolling(5).mean().clip(-1, 1)
        return result
