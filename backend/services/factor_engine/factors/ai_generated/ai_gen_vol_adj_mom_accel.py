"""AI因子: 波动调整动量加速度 | 置信:60% | 用短期动量减去长期动量得到动量加速度，再除以近期已实现波动率做标准化。正值表示上涨动能加速且波动可控，预示未来继续上行；负值表示动能衰减或下行加速。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """用短期动量减去长期动量得到动量加速度，再除以近期已实现波动率做标准化。正值表示上涨动能加速且波动可控，预示未来继续上行；负值表示动能衰减或下行加速。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_adj_mom_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="用短期动量减去长期动量得到动量加速度，再除以近期已实现波动率做标准化。正值表示上涨动能加速且波动可控，预示未来继续上行；负值表示动能衰减或下行加速。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_mom = data['close'].pct_change(5)
        long_mom = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short_mom - long_mom) / vol).clip(-1, 1)
        return result
