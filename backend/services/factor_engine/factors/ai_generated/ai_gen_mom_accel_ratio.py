"""AI因子: 动量加速度比率 | 置信:60% | 用短周期收益与长周期收益的差值衡量动量加速度，再除以近期波动率做标准化。正值表示短期动量强于长期趋势（加速上行），负值表示动量衰减。捕捉趋势延续与拐点前兆。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationRatio(BaseFactor):
    """用短周期收益与长周期收益的差值衡量动量加速度，再除以近期波动率做标准化。正值表示短期动量强于长期趋势（加速上行），负值表示动量衰减。捕捉趋势延续与拐点前兆。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_ratio",
            name="Momentum Acceleration Ratio",
            display_name="动量加速度比率",
            description="用短周期收益与长周期收益的差值衡量动量加速度，再除以近期波动率做标准化。正值表示短期动量强于长期趋势（加速上行），负值表示动量衰减。捕捉趋势延续与拐点前兆。",
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
