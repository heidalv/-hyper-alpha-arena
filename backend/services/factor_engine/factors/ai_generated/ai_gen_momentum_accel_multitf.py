"""AI因子: 多周期动量加速度 | 置信:60% | 用短周期收益率与长周期收益率之差衡量动量加速度，再除以近期波动率归一化，正值代表短期动量强于长期趋势，预期未来延续上涨。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumAcceleration(BaseFactor):
    """用短周期收益率与长周期收益率之差衡量动量加速度，再除以近期波动率归一化，正值代表短期动量强于长期趋势，预期未来延续上涨。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_multitf",
            name="Multi-Timeframe Momentum Acceleration",
            display_name="多周期动量加速度",
            description="用短周期收益率与长周期收益率之差衡量动量加速度，再除以近期波动率归一化，正值代表短期动量强于长期趋势，预期未来延续上涨。",
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
