"""AI因子: 插针密度波动环境 | 置信:55% | 以 max(upper,lower)/body 的20日滚动均值度量影线密度，即市场插针/波动环境分位。高密度代表噪声与假突破频发，短线收益更易均值回归；将其与短期收益方向交互，构造反转型alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityVolatilityRegime(BaseFactor):
    """以 max(upper,lower)/body 的20日滚动均值度量影线密度，即市场插针/波动环境分位。高密度代表噪声与假突破频发，短线收益更易均值回归；将其与短期收益方向交互，构造反转型alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_vol_regime",
            name="Pin Density Volatility Regime",
            display_name="插针密度波动环境",
            description="以 max(upper,lower)/body 的20日滚动均值度量影线密度，即市场插针/波动环境分位。高密度代表噪声与假突破频发，短线收益更易均值回归；将其与短期收益方向交互，构造反转型alpha。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1))
        pin = (upper + lower) / body
        ret = data['close'].pct_change(3)
        result = (-(ret * pin.rolling(20).mean())).clip(-1, 1)
        return result
