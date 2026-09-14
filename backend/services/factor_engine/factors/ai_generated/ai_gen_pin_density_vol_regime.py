"""AI因子: 插针密度波动环境因子 | 置信:58% | 插针密度 = max(upper,lower)/body 的20日滚动均值，衡量影线主导的高波动环境。在高插针密度环境下，短期收益更容易均值回归；低密度环境则动量延续。因子用密度分位与短期收益反向交互，捕捉波动环境切换下的收益方向。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityVolatilityRegime(BaseFactor):
    """插针密度 = max(upper,lower)/body 的20日滚动均值，衡量影线主导的高波动环境。在高插针密度环境下，短期收益更容易均值回归；低密度环境则动量延续。因子用密度分位与短期收益反向交互，捕捉波动环境切换下的收益方向。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_vol_regime",
            name="Pin Density Volatility Regime",
            display_name="插针密度波动环境因子",
            description="插针密度 = max(upper,lower)/body 的20日滚动均值，衡量影线主导的高波动环境。在高插针密度环境下，短期收益更容易均值回归；低密度环境则动量延续。因子用密度分位与短期收益反向交互，捕捉波动环境切换下的收益方向。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (upper.combine(lower, max) / body).rolling(20).mean()
        pin_z = (pin - pin.rolling(60).mean()) / (pin.rolling(60).std() + 1e-9)
        ret5 = data['close'].pct_change(5)
        result = (-pin_z.clip(-1, 1) * ret5.clip(-1, 1)).clip(-1, 1)
        return result
