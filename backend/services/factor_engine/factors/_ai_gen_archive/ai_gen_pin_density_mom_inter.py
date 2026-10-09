"""AI因子: 插针密度与动量交互 | 置信:58% | 以(max(upper,lower)/body)的20期滚动均值刻画插针密度环境，密度高说明多空博弈激烈、易出现反转。将插针密度与短期收益方向交互：高密度环境下短期动量更易反转，故取负号，形成可测IC的插针反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentumInteraction(BaseFactor):
    """以(max(upper,lower)/body)的20期滚动均值刻画插针密度环境，密度高说明多空博弈激烈、易出现反转。将插针密度与短期收益方向交互：高密度环境下短期动量更易反转，故取负号，形成可测IC的插针反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mom_inter",
            name="Pin Density Momentum Interaction",
            display_name="插针密度与动量交互",
            description="以(max(upper,lower)/body)的20期滚动均值刻画插针密度环境，密度高说明多空博弈激烈、易出现反转。将插针密度与短期收益方向交互：高密度环境下短期动量更易反转，故取负号，形成可测IC的插针反转因子。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        density = (np.maximum(upper, lower) / body).rolling(20).mean()
        mom = data['close'].pct_change(3)
        result = (-density * mom).rolling(3).mean().clip(-1, 1)
        return result
