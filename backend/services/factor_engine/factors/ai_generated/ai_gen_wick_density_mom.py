"""AI因子: 影线密度动量交互 | 置信:58% | 插针密度（上下影线相对实体的均值）刻画市场波动环境，高密度意味着多空分歧剧烈。将插针密度与短期收益方向交互：在高密度环境下，短期反转效应更强。因子= 密度分位 * (-短期收益)，捕捉高波动插针环境下的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class WickDensityMomentumInteraction(BaseFactor):
    """插针密度（上下影线相对实体的均值）刻画市场波动环境，高密度意味着多空分歧剧烈。将插针密度与短期收益方向交互：在高密度环境下，短期反转效应更强。因子= 密度分位 * (-短期收益)，捕捉高波动插针环境下的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_wick_density_mom",
            name="Wick Density Momentum Interaction",
            display_name="影线密度动量交互",
            description="插针密度（上下影线相对实体的均值）刻画市场波动环境，高密度意味着多空分歧剧烈。将插针密度与短期收益方向交互：在高密度环境下，短期反转效应更强。因子= 密度分位 * (-短期收益)，捕捉高波动插针环境下的均值回归alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['high','low']].max(axis=1) - data[['high','low']].min(axis=1)) / body
        dens_ma = density.rolling(20).mean()
        ret5 = data['close'].pct_change(5)
        result = (-ret5 * dens_ma).rolling(3).mean().clip(-1, 1)
        return result
