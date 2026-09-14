"""AI因子: 波动率调整动量加速度 | 置信:60% | 多周期动量差（短周期收益减长周期收益）除以近期波动率，衡量动量加速度的相对强度，正值代表短期动能强于中期趋势，未来延续上涨概率更高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """多周期动量差（短周期收益减长周期收益）除以近期波动率，衡量动量加速度的相对强度，正值代表短期动能强于中期趋势，未来延续上涨概率更高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_vol_adj",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动率调整动量加速度",
            description="多周期动量差（短周期收益减长周期收益）除以近期波动率，衡量动量加速度的相对强度，正值代表短期动能强于中期趋势，未来延续上涨概率更高。",
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
