"""AI因子: 插针密度加权反转 | 置信:58% | 以插针密度（上下影线相对实体的强度20期均值）作为波动环境权重，乘以短期收益方向的反转信号。高插针密度环境下短期反转更可靠，因子值域[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinbarDensityWeightedReversal(BaseFactor):
    """以插针密度（上下影线相对实体的强度20期均值）作为波动环境权重，乘以短期收益方向的反转信号。高插针密度环境下短期反转更可靠，因子值域[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pinbar_density_rev",
            name="Pinbar Density Weighted Reversal",
            display_name="插针密度加权反转",
            description="以插针密度（上下影线相对实体的强度20期均值）作为波动环境权重，乘以短期收益方向的反转信号。高插针密度环境下短期反转更可靠，因子值域[-1,1]。",
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
        ret5 = data['close'].pct_change(5)
        result = (-ret5 * density).clip(-1, 1)
        return result
