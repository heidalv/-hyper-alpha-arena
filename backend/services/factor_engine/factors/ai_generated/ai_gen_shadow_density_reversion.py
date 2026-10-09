"""AI因子: 影线密度均值回归 | 置信:55% | 插针密度（上下影线相对实体的平均幅度）刻画市场情绪化波动环境。高密度环境下价格易过度反应，短期收益与影线方向背离后倾向均值回归。用密度分位与短期收益的负向交互构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShadowDensityReversion(BaseFactor):
    """插针密度（上下影线相对实体的平均幅度）刻画市场情绪化波动环境。高密度环境下价格易过度反应，短期收益与影线方向背离后倾向均值回归。用密度分位与短期收益的负向交互构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_shadow_density_reversion",
            name="Shadow Density Reversion",
            display_name="影线密度均值回归",
            description="插针密度（上下影线相对实体的平均幅度）刻画市场情绪化波动环境。高密度环境下价格易过度反应，短期收益与影线方向背离后倾向均值回归。用密度分位与短期收益的负向交互构造反转因子。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1)).rolling(20).mean()
        pin = (upper + lower) / body
        ret = data['close'].pct_change(5)
        result = (-ret * (pin.rolling(20).mean() / (density + 1e-9))).clip(-1, 1)
        return result
