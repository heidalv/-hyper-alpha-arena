"""AI因子: 插针密度门控动量 | 置信:58% | 以插针密度(上下影线相对实体的大小20期均值)作为波动/插针环境分位，在插针密集环境下短期动量更容易被反转，因此用密度分位对短期动量做反向门控，输出[-1,1]的可测IC因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityGatedMomentum(BaseFactor):
    """以插针密度(上下影线相对实体的大小20期均值)作为波动/插针环境分位，在插针密集环境下短期动量更容易被反转，因此用密度分位对短期动量做反向门控，输出[-1,1]的可测IC因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_momentum_gate",
            name="Pin Density Gated Momentum",
            display_name="插针密度门控动量",
            description="以插针密度(上下影线相对实体的大小20期均值)作为波动/插针环境分位，在插针密集环境下短期动量更容易被反转，因此用密度分位对短期动量做反向门控，输出[-1,1]的可测IC因子。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (upper + lower) / body
        density = pin.rolling(20).mean()
        mom = data['close'].pct_change(5)
        result = (-mom * density).clip(-1, 1)
        return result
