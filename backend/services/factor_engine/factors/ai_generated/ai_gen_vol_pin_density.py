"""AI因子: 插针密度波动环境 | 置信:58% | 以最大影线/实体比值20周期均值刻画插针密度环境，密度越高代表多空博弈激烈、均值回归概率上升；再与短期收益反向交互，捕捉高密度环境下的反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityVolatilityRegime(BaseFactor):
    """以最大影线/实体比值20周期均值刻画插针密度环境，密度越高代表多空博弈激烈、均值回归概率上升；再与短期收益反向交互，捕捉高密度环境下的反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_pin_density",
            name="Pin Density Volatility Regime",
            display_name="插针密度波动环境",
            description="以最大影线/实体比值20周期均值刻画插针密度环境，密度越高代表多空博弈激烈、均值回归概率上升；再与短期收益反向交互，捕捉高密度环境下的反转alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1))
        density = (data['high'] - data['low']) / (body + 1e-9)
        env = density.rolling(20).mean()
        ret = data['close'].pct_change(3)
        result = (-ret * env / (env.rolling(20).std() + 1e-9)).clip(-1, 1)
        return result
