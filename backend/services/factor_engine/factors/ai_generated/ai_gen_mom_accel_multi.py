"""AI因子: 多周期动量加速度 | 置信:58% | 短期动量与长期动量之差衡量动量加速度,正值代表近期动能增强,预期延续上涨;负值代表动能衰减。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAcceleration(BaseFactor):
    """短期动量与长期动量之差衡量动量加速度,正值代表近期动能增强,预期延续上涨;负值代表动能衰减。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multi",
            name="Multi-Horizon Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与长期动量之差衡量动量加速度,正值代表近期动能增强,预期延续上涨;负值代表动能衰减。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((fast - slow) / (vol + 1e-9)).clip(-1, 1)
        return result
