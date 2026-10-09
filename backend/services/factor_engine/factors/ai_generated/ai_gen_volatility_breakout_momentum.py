"""AI因子: 波动调整动量加速度 | 置信:60% | 用短周期动量减去长周期动量衡量加速度，并以已实现波动率归一化，捕捉趋势加速阶段的延续性；波动调整后信号更稳定，避免高波动噪声主导。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """用短周期动量减去长周期动量衡量加速度，并以已实现波动率归一化，捕捉趋势加速阶段的延续性；波动调整后信号更稳定，避免高波动噪声主导。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatility_breakout_momentum",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="用短周期动量减去长周期动量衡量加速度，并以已实现波动率归一化，捕捉趋势加速阶段的延续性；波动调整后信号更稳定，避免高波动噪声主导。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom_fast = data['close'].pct_change(5)
        mom_slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((mom_fast - mom_slow) / vol).clip(-1, 1)
        return result
