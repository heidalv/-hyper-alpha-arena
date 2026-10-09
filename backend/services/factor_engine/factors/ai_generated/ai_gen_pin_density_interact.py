"""AI因子: 插针密度加权影线反转 | 置信:58% | 先用上下影线相对实体的最大值构造插针密度环境（20周期均值），再与影线不对称度交互。在高插针密度环境下，影线不对称反转信号更强，用于捕捉震荡市中的短线反转 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedAsymmetry(BaseFactor):
    """先用上下影线相对实体的最大值构造插针密度环境（20周期均值），再与影线不对称度交互。在高插针密度环境下，影线不对称反转信号更强，用于捕捉震荡市中的短线反转 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_interact",
            name="Pin Density Weighted Asymmetry",
            display_name="插针密度加权影线反转",
            description="先用上下影线相对实体的最大值构造插针密度环境（20周期均值），再与影线不对称度交互。在高插针密度环境下，影线不对称反转信号更强，用于捕捉震荡市中的短线反转 alpha。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = (lower - upper) / body
        density = (data[['high','low']].max(axis=1) - data[['high','low']].min(axis=1)) / body
        result = (asym * density.rolling(20).mean()).rolling(3).mean().clip(-1, 1)
        return result
