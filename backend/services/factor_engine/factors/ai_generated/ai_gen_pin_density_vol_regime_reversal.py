"""AI因子: 插针密度波动环境反转 | 置信:58% | 利用插针密度(影线/实体比值的20期均值)作为波动环境分位，在高插针密度环境下短期反转效应更强。因子为插针密度与短期收益负向交互，捕捉影线密集环境下的均值回归。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityVolRegimeReversal(BaseFactor):
    """利用插针密度(影线/实体比值的20期均值)作为波动环境分位，在高插针密度环境下短期反转效应更强。因子为插针密度与短期收益负向交互，捕捉影线密集环境下的均值回归。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_vol_regime_reversal",
            name="Pin Density Vol Regime Reversal",
            display_name="插针密度波动环境反转",
            description="利用插针密度(影线/实体比值的20期均值)作为波动环境分位，在高插针密度环境下短期反转效应更强。因子为插针密度与短期收益负向交互，捕捉影线密集环境下的均值回归。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin_density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1) + 1e-9)
        density = (upper + lower) / body
        density_ma = density.rolling(20).mean()
        short_ret = data['close'].pct_change(5)
        result = (-1 * density_ma * short_ret).clip(-1, 1)
        return result
