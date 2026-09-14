"""AI因子: 插针密度均值回归 | 置信:58% | 插针密度(影线/实体比值的滚动均值)刻画市场拒绝与波动环境。高密度环境下价格易过度反应，短期收益反向修正概率高。用密度分位与短期收益反向交互构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMeanReversion(BaseFactor):
    """插针密度(影线/实体比值的滚动均值)刻画市场拒绝与波动环境。高密度环境下价格易过度反应，短期收益反向修正概率高。用密度分位与短期收益反向交互构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_rev",
            name="Pin Density Mean Reversion",
            display_name="插针密度均值回归",
            description="插针密度(影线/实体比值的滚动均值)刻画市场拒绝与波动环境。高密度环境下价格易过度反应，短期收益反向修正概率高。用密度分位与短期收益反向交互构造反转因子。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        ret = data['close'].pct_change(5)
        result = (density * (-ret)).clip(-1, 1)
        return result
