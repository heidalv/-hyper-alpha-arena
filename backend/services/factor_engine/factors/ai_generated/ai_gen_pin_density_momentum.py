"""AI因子: 插针密度加权短期动量 | 置信:58% | 在高插针密度（影线主导的震荡环境）下，短期收益更容易均值回归。用20日插针密度作为环境权重，与5日收益方向交互，密度越高则反转信号越强，捕捉震荡市中的短期反转 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedShortMomentum(BaseFactor):
    """在高插针密度（影线主导的震荡环境）下，短期收益更容易均值回归。用20日插针密度作为环境权重，与5日收益方向交互，密度越高则反转信号越强，捕捉震荡市中的短期反转 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_momentum",
            name="Pin Density Weighted Short Momentum",
            display_name="插针密度加权短期动量",
            description="在高插针密度（影线主导的震荡环境）下，短期收益更容易均值回归。用20日插针密度作为环境权重，与5日收益方向交互，密度越高则反转信号越强，捕捉震荡市中的短期反转 alpha。",
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
        ret = data['close'].pct_change(5)
        result = (-ret * density).clip(-1, 1)
        return result
