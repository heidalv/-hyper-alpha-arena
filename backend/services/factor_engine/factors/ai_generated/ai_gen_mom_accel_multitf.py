"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与中期动量之差，衡量动量加速度。正值表示短期动能强于中期，趋势加速上行；负值表示动能衰减或下行加速。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumAcceleration(BaseFactor):
    """短期动量与中期动量之差，衡量动量加速度。正值表示短期动能强于中期，趋势加速上行；负值表示动能衰减或下行加速。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multitf",
            name="Multi-Timeframe Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与中期动量之差，衡量动量加速度。正值表示短期动能强于中期，趋势加速上行；负值表示动能衰减或下行加速。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short - long) / vol).clip(-1, 1)
        return result
