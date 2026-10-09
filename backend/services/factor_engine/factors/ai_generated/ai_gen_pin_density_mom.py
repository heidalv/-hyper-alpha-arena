"""AI因子: 插针密度动量 | 置信:58% | 以影线/实体比值的滚动均值衡量插针密度环境，高密度代表多空博弈剧烈、价格易被拒绝。将插针密度与短期动量方向结合：高密度环境下动量更易反转，低密度环境下动量更易延续，形成可测IC的复合因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PinDensityMomentum(BaseFactor):
    """以影线/实体比值的滚动均值衡量插针密度环境，高密度代表多空博弈剧烈、价格易被拒绝。将插针密度与短期动量方向结合：高密度环境下动量更易反转，低密度环境下动量更易延续，形成可测IC的复合因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pin_density_mom",
            name="Pin Density Momentum",
            display_name="插针密度动量",
            description="以影线/实体比值的滚动均值衡量插针密度环境，高密度代表多空博弈剧烈、价格易被拒绝。将插针密度与短期动量方向结合：高密度环境下动量更易反转，低密度环境下动量更易延续，形成可测IC的复合因子。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        body = (data['close'] - data['open']).abs() + 1e-9
        upper = data['high'] - data[['open','close']].max(axis=1)
        lower = data[['open','close']].min(axis=1) - data['low']
        pin = (np.maximum(upper, lower) / body).rolling(20).mean()
        mom = data['close'].pct_change(5)
        result = (mom * (1 - pin.rolling(5).mean())).clip(-1, 1)
        return result
