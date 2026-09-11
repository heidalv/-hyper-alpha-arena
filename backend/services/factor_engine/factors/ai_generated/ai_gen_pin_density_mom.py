"""AI因子: 插针密度加权动量 | 置信:55% | 插针密度(影线/实体比)反映市场分歧与波动环境。在高插针密度环境下，短期动量更易被反向修正；在低密度趋势环境下动量延续。用密度分位作为权重调节短期收益方向，构造环境自适应的动量因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedMomentum(BaseFactor):
    """插针密度(影线/实体比)反映市场分歧与波动环境。在高插针密度环境下，短期动量更易被反向修正；在低密度趋势环境下动量延续。用密度分位作为权重调节短期收益方向，构造环境自适应的动量因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mom",
            name="Pin Density Weighted Momentum",
            display_name="插针密度加权动量",
            description="插针密度(影线/实体比)反映市场分歧与波动环境。在高插针密度环境下，短期动量更易被反向修正；在低密度趋势环境下动量延续。用密度分位作为权重调节短期收益方向，构造环境自适应的动量因子。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['high','low']].max(axis=1) - data[['high','low']].min(axis=1)) / body
        dens_ma = density.rolling(20).mean()
        ret = data['close'].pct_change(5)
        result = (ret * (dens_ma - dens_ma.rolling(60).mean())).clip(-1, 1)
        return result
