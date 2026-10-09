"""AI因子: 影线密度均值回归 | 置信:58% | 插针密度(上下影线相对实体的均值)刻画市场情绪化程度。高密度环境下价格易超调，用短期收益的负向作为反转信号，密度作为放大系数，构造环境自适应的反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickDensityMeanReversion(BaseFactor):
    """插针密度(上下影线相对实体的均值)刻画市场情绪化程度。高密度环境下价格易超调，用短期收益的负向作为反转信号，密度作为放大系数，构造环境自适应的反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_density_mr",
            name="Wick Density Mean Reversion",
            display_name="影线密度均值回归",
            description="插针密度(上下影线相对实体的均值)刻画市场情绪化程度。高密度环境下价格易超调，用短期收益的负向作为反转信号，密度作为放大系数，构造环境自适应的反转因子。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1) + upper + lower) / body
        ret = data['close'].pct_change(5)
        result = (-ret * density.rolling(20).mean()).clip(-1, 1)
        return result
