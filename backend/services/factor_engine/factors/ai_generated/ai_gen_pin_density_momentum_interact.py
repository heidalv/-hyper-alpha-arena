"""AI因子: 插针密度与动量交互 | 置信:58% | 以插针密度（影线/实体比值的20期均值）作为波动环境代理，与短期动量方向交互。高插针密度环境下动量更容易反转，因此用密度对动量做反向调制，形成可测IC的复合因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """以插针密度（影线/实体比值的20期均值）作为波动环境代理，与短期动量方向交互。高插针密度环境下动量更容易反转，因此用密度对动量做反向调制，形成可测IC的复合因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_momentum_interact",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与动量交互",
            description="以插针密度（影线/实体比值的20期均值）作为波动环境代理，与短期动量方向交互。高插针密度环境下动量更容易反转，因此用密度对动量做反向调制，形成可测IC的复合因子。",
            category="composite",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (np.maximum(upper, lower) / body).rolling(20).mean()
        mom = data['close'].pct_change(5)
        result = (-mom * pin).clip(-1, 1)
        return result
