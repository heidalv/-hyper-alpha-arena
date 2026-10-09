"""AI因子: 波动调整动量加速度 | 置信:60% | 多周期动量差（5日减20日收益）衡量动量加速度，除以已实现波动率做标准化，正值代表近期动量加速上行，负值代表动量衰减或反转，捕捉趋势延续与拐点。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolScaled(BaseFactor):
    """多周期动量差（5日减20日收益）衡量动量加速度，除以已实现波动率做标准化，正值代表近期动量加速上行，负值代表动量衰减或反转，捕捉趋势延续与拐点。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol",
            name="Momentum Acceleration Vol-Scaled",
            display_name="波动调整动量加速度",
            description="多周期动量差（5日减20日收益）衡量动量加速度，除以已实现波动率做标准化，正值代表近期动量加速上行，负值代表动量衰减或反转，捕捉趋势延续与拐点。",
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
