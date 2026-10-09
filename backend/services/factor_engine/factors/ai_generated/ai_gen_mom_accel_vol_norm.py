"""AI因子: 波动率归一化动量加速度 | 置信:60% | 以短期动量(5期收益)减去长期动量(20期收益)衡量动量加速度，再除以20期已实现波动率做归一化。正值表示近期动量相对长期加速且波动可控，未来延续上涨概率高；负值表示动量衰减，未来回落概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolatilityNormalized(BaseFactor):
    """以短期动量(5期收益)减去长期动量(20期收益)衡量动量加速度，再除以20期已实现波动率做归一化。正值表示近期动量相对长期加速且波动可控，未来延续上涨概率高；负值表示动量衰减，未来回落概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol_norm",
            name="Momentum Acceleration Volatility Normalized",
            display_name="波动率归一化动量加速度",
            description="以短期动量(5期收益)减去长期动量(20期收益)衡量动量加速度，再除以20期已实现波动率做归一化。正值表示近期动量相对长期加速且波动可控，未来延续上涨概率高；负值表示动量衰减，未来回落概率高。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_short = data['close'].pct_change(5)
        mom_long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((mom_short - mom_long) / (vol + 1e-9)).clip(-1, 1)
        return result
