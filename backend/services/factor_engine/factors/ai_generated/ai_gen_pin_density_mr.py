"""AI因子: 插针密度环境均值回归 | 置信:58% | 插针密度(上下影线相对实体的均值)刻画市场情绪波动环境。高密度环境意味着多空博弈剧烈，价格易出现超调后回归。用短期收益与插针密度的分位交互，捕捉高波动环境下的反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMeanReversion(BaseFactor):
    """插针密度(上下影线相对实体的均值)刻画市场情绪波动环境。高密度环境意味着多空博弈剧烈，价格易出现超调后回归。用短期收益与插针密度的分位交互，捕捉高波动环境下的反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mr",
            name="Pin Density Mean Reversion",
            display_name="插针密度环境均值回归",
            description="插针密度(上下影线相对实体的均值)刻画市场情绪波动环境。高密度环境意味着多空博弈剧烈，价格易出现超调后回归。用短期收益与插针密度的分位交互，捕捉高波动环境下的反转alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (upper + lower) / body
        dens = pin.rolling(20).mean()
        dens_z = (dens - dens.rolling(60).mean()) / (dens.rolling(60).std() + 1e-9)
        ret = data['close'].pct_change(3)
        result = (-ret * dens_z).clip(-1, 1)
        return result
