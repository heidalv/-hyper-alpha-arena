"""AI因子: 插针密度与动量交互 | 置信:58% | 以影线密度（上下影线相对实体的均值）刻画插针环境，将插针不对称度滚动均值与短期收益方向交互。在高插针密度环境下，短期动量方向更易被反转，从而产生可测IC。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """以影线密度（上下影线相对实体的均值）刻画插针环境，将插针不对称度滚动均值与短期收益方向交互。在高插针密度环境下，短期动量方向更易被反转，从而产生可测IC。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mom_inter",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与动量交互",
            description="以影线密度（上下影线相对实体的均值）刻画插针环境，将插针不对称度滚动均值与短期收益方向交互。在高插针密度环境下，短期动量方向更易被反转，从而产生可测IC。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = ((lower - upper) / body).rolling(5).mean()
        density = (data[['open','close']].max(axis=1).sub(data[['open','close']].min(axis=1)).add(1e-9))
        pin_density = (upper.add(lower).div(density)).rolling(20).mean()
        mom = data['close'].pct_change(5)
        result = (asym * (1 - pin_density.clip(0, 1)) - mom * pin_density.clip(0, 1)).clip(-1, 1)
        return result
