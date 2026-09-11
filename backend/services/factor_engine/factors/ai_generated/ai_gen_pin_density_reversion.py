"""AI因子: 插针密度环境均值回归 | 置信:58% | 插针密度(影线/实体比值的20期滚动均值)刻画市场处于高波动插针环境。在高密度环境下，短期收益更容易均值回归；用密度对短期收益做反向加权，得到环境自适应的反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMeanReversion(BaseFactor):
    """插针密度(影线/实体比值的20期滚动均值)刻画市场处于高波动插针环境。在高密度环境下，短期收益更容易均值回归；用密度对短期收益做反向加权，得到环境自适应的反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_reversion",
            name="Pin Density Mean Reversion",
            display_name="插针密度环境均值回归",
            description="插针密度(影线/实体比值的20期滚动均值)刻画市场处于高波动插针环境。在高密度环境下，短期收益更容易均值回归；用密度对短期收益做反向加权，得到环境自适应的反转因子。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (data[['high','low']].max(axis=1) - data[['high','low']].min(axis=1)) / body
        dens = (pin / (pin.rolling(20).mean() + 1e-9)).rolling(5).mean()
        ret = data['close'].pct_change(5)
        result = (-ret * dens).clip(-1, 1)
        return result
