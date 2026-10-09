"""AI因子: 多周期动量加速度 | 置信:60% | 以短周期收益与长周期收益之差衡量动量加速度，并用波动率归一化。加速度为正说明近期动能强于中期基准，未来延续上涨概率更高；为负则预示回落。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAcceleration(BaseFactor):
    """以短周期收益与长周期收益之差衡量动量加速度，并用波动率归一化。加速度为正说明近期动能强于中期基准，未来延续上涨概率更高；为负则预示回落。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_multi",
            name="Multi-Horizon Momentum Acceleration",
            display_name="多周期动量加速度",
            description="以短周期收益与长周期收益之差衡量动量加速度，并用波动率归一化。加速度为正说明近期动能强于中期基准，未来延续上涨概率更高；为负则预示回落。",
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
