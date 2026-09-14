"""AI因子: 插针密度波动环境因子 | 置信:58% | 用影线相对实体的密度衡量市场插针活跃度，密度高时短期反转更强。将密度分位与短期收益反向交互，捕捉高噪声环境下的均值回归 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinBarDensityVolatilityRegime(BaseFactor):
    """用影线相对实体的密度衡量市场插针活跃度，密度高时短期反转更强。将密度分位与短期收益反向交互，捕捉高噪声环境下的均值回归 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_vol_regime",
            name="Pin Bar Density Volatility Regime",
            display_name="插针密度波动环境因子",
            description="用影线相对实体的密度衡量市场插针活跃度，密度高时短期反转更强。将密度分位与短期收益反向交互，捕捉高噪声环境下的均值回归 alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1) + 1e-9)
        pin = (upper + lower) / body
        dens = pin.rolling(20).mean()
        ret = data['close'].pct_change(3)
        result = (dens.rank(pct=True) * (-ret).clip(-1, 1)).rolling(5).mean().clip(-1, 1)
        return result
