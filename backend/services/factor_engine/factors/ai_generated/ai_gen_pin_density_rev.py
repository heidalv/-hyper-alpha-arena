"""AI因子: 插针密度加权反转 | 置信:58% | 先计算单根K线的插针强度（上下影线相对实体），再以20日滚动均值刻画插针密集环境。在插针密集环境下，短期收益方向更易反转，因此用负的短期收益乘以插针密度分位作为因子，捕捉高波动插针环境下的均值回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """先计算单根K线的插针强度（上下影线相对实体），再以20日滚动均值刻画插针密集环境。在插针密集环境下，短期收益方向更易反转，因此用负的短期收益乘以插针密度分位作为因子，捕捉高波动插针环境下的均值回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_rev",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="先计算单根K线的插针强度（上下影线相对实体），再以20日滚动均值刻画插针密集环境。在插针密集环境下，短期收益方向更易反转，因此用负的短期收益乘以插针密度分位作为因子，捕捉高波动插针环境下的均值回归。",
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
        ret = data['close'].pct_change(3)
        result = (-ret * density).clip(-1, 1)
        return result
