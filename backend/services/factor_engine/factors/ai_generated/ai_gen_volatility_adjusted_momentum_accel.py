"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量差（5日减20日收益）刻画动量加速度，再用已实现波动率归一化，避免高波动品种主导。加速度为正说明短期动量强于中期，趋势延续概率高；为负则动量衰竭，反转概率上升。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量差（5日减20日收益）刻画动量加速度，再用已实现波动率归一化，避免高波动品种主导。加速度为正说明短期动量强于中期，趋势延续概率高；为负则动量衰竭，反转概率上升。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_adjusted_momentum_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="多周期动量差（5日减20日收益）刻画动量加速度，再用已实现波动率归一化，避免高波动品种主导。加速度为正说明短期动量强于中期，趋势延续概率高；为负则动量衰竭，反转概率上升。",
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
