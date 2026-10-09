"""AI因子: 插针密度与动量交互 | 置信:58% | 先度量插针密度环境（上下影线相对实体的均值），再与短期收益方向相乘。高插针密度环境下短期动量更易被反向修正，因子值方向与短期收益相反，捕捉影线密集区的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """先度量插针密度环境（上下影线相对实体的均值），再与短期收益方向相乘。高插针密度环境下短期动量更易被反向修正，因子值方向与短期收益相反，捕捉影线密集区的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mom_interact",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与动量交互",
            description="先度量插针密度环境（上下影线相对实体的均值），再与短期收益方向相乘。高插针密度环境下短期动量更易被反向修正，因子值方向与短期收益相反，捕捉影线密集区的均值回归alpha。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1) + 1e-9)
        pin = (upper + lower) / body
        mom = data['close'].pct_change(3)
        result = (-mom * pin.rolling(5).mean()).clip(-1, 1)
        return result
