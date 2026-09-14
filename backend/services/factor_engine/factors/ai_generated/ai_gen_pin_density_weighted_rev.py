"""AI因子: 插针密度加权反转 | 置信:58% | 用插针密度（影线相对实体的比例滚动均值）衡量当前波动环境，并将影线不对称度与短期收益方向交互：在高插针密度环境下，近期下跌伴随下影承接时给出正向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityWeightedReversal(BaseFactor):
    """用插针密度（影线相对实体的比例滚动均值）衡量当前波动环境，并将影线不对称度与短期收益方向交互：在高插针密度环境下，近期下跌伴随下影承接时给出正向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_weighted_rev",
            name="Pin Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="用插针密度（影线相对实体的比例滚动均值）衡量当前波动环境，并将影线不对称度与短期收益方向交互：在高插针密度环境下，近期下跌伴随下影承接时给出正向信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        asym = ((lower - upper) / body).rolling(5).mean()
        density = (data[['high','low']].max(axis=1) - data[['high','low']].min(axis=1)) / body
        dens = density.rolling(20).mean()
        ret = data['close'].pct_change(3)
        result = (asym * (dens / (dens.rolling(20).mean() + 1e-9)) - ret * 5).clip(-1, 1)
        return result
