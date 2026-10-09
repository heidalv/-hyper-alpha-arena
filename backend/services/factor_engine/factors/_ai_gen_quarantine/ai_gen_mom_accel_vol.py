"""AI因子: 波动率调整动量加速度 | 置信:58% | 用短周期动量(5日)减去长周期动量(20日)构造动量加速度，除以已实现波动率做风险归一化。捕捉趋势加速阶段的方向性 alpha，波动率归一化避免高波动币种主导因子值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolScaled(BaseFactor):
    """用短周期动量(5日)减去长周期动量(20日)构造动量加速度，除以已实现波动率做风险归一化。捕捉趋势加速阶段的方向性 alpha，波动率归一化避免高波动币种主导因子值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_vol",
            name="Momentum Acceleration Vol-Scaled",
            display_name="波动率调整动量加速度",
            description="用短周期动量(5日)减去长周期动量(20日)构造动量加速度，除以已实现波动率做风险归一化。捕捉趋势加速阶段的方向性 alpha，波动率归一化避免高波动币种主导因子值。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mom = data['close'].pct_change(5) - data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = (mom / vol).clip(-1, 1)
        return result
