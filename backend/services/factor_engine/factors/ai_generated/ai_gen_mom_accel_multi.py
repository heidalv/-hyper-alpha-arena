"""AI因子: 多周期动量加速度 | 置信:60% | 用短周期收益减长周期收益衡量动量加速度。差值为正说明近期动能强于中期趋势，趋势加速上行；为负说明动能衰减。标准化后作为方向性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAcceleration(BaseFactor):
    """用短周期收益减长周期收益衡量动量加速度。差值为正说明近期动能强于中期趋势，趋势加速上行；为负说明动能衰减。标准化后作为方向性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_multi",
            name="Multi-Horizon Momentum Acceleration",
            display_name="多周期动量加速度",
            description="用短周期收益减长周期收益衡量动量加速度。差值为正说明近期动能强于中期趋势，趋势加速上行；为负说明动能衰减。标准化后作为方向性 alpha。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((fast - slow) / vol).clip(-1, 1)
        return result
