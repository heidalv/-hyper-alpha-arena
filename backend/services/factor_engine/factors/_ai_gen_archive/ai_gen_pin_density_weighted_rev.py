"""AI因子: 插针密度加权反转 | 置信:58% | 用插针密度（影线/实体比值的20期均值）作为波动环境分位，对影线不对称度做加权。高插针密度环境下影线信号更可靠，因此放大不对称度信号；低密度环境（趋势顺畅）则衰减信号，从而在震荡插针行情中捕捉均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """用插针密度（影线/实体比值的20期均值）作为波动环境分位，对影线不对称度做加权。高插针密度环境下影线信号更可靠，因此放大不对称度信号；低密度环境（趋势顺畅）则衰减信号，从而在震荡插针行情中捕捉均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_weighted_rev",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="用插针密度（影线/实体比值的20期均值）作为波动环境分位，对影线不对称度做加权。高插针密度环境下影线信号更可靠，因此放大不对称度信号；低密度环境（趋势顺畅）则衰减信号，从而在震荡插针行情中捕捉均值回归alpha。",
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
        density = (data['high'] - data['low']) / body
        dens_mean = density.rolling(20).mean()
        result = (asym * dens_mean).rolling(3).mean().clip(-1, 1)
        return result
