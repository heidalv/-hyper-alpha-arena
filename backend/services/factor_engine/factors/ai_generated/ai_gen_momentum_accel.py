"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与中期动量的差值反映动量加速度，正值表示近期动能强于中期趋势，预示延续上涨；负值预示下跌。用波动率归一化以稳定量纲。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期动量与中期动量的差值反映动量加速度，正值表示近期动能强于中期趋势，预示延续上涨；负值预示下跌。用波动率归一化以稳定量纲。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel",
            name="Multi-Period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与中期动量的差值反映动量加速度，正值表示近期动能强于中期趋势，预示延续上涨；负值预示下跌。用波动率归一化以稳定量纲。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((short - long) / (vol + 1e-9)).clip(-1, 1)
        return result
