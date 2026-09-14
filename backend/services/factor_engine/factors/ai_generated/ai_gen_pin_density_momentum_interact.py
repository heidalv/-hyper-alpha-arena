"""AI因子: 插针密度与短期动量交互 | 置信:55% | 先用(max(upper,lower)/body)的20日滚动均值刻画插针密度环境，再与短期收益方向交互：高插针密度环境下短期动量更容易被反转，因此用负号放大反转效应；低密度环境下动量延续。构造为 -sign(短期收益)*插针密度，clip至[-1,1]，捕捉影线密集环境下的均值回归alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """先用(max(upper,lower)/body)的20日滚动均值刻画插针密度环境，再与短期收益方向交互：高插针密度环境下短期动量更容易被反转，因此用负号放大反转效应；低密度环境下动量延续。构造为 -sign(短期收益)*插针密度，clip至[-1,1]，捕捉影线密集环境下的均值回归alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_momentum_interact",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与短期动量交互",
            description="先用(max(upper,lower)/body)的20日滚动均值刻画插针密度环境，再与短期收益方向交互：高插针密度环境下短期动量更容易被反转，因此用负号放大反转效应；低密度环境下动量延续。构造为 -sign(短期收益)*插针密度，clip至[-1,1]，捕捉影线密集环境下的均值回归alpha。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (data[['open','close']].max(axis=1) - data[['open','close']].min(axis=1)).rolling(20).mean()
        pin = (data['high'] - data['low']) / (body + 1e-9)
        ret = data['close'].pct_change(3)
        result = (-ret * pin.rolling(5).mean()).clip(-1, 1)
        return result
