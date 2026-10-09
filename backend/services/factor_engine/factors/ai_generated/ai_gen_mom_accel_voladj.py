"""AI因子: 波动调整动量加速度 | 置信:60% | 短期动量与中期动量之差衡量加速度，除以已实现波动率做风险调整，捕捉趋势加速阶段的持续性收益，同时抑制高波动噪声。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolatilityAdjusted(BaseFactor):
    """短期动量与中期动量之差衡量加速度，除以已实现波动率做风险调整，捕捉趋势加速阶段的持续性收益，同时抑制高波动噪声。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_voladj",
            name="Momentum Acceleration Volatility Adjusted",
            display_name="波动调整动量加速度",
            description="短期动量与中期动量之差衡量加速度，除以已实现波动率做风险调整，捕捉趋势加速阶段的持续性收益，同时抑制高波动噪声。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((short - long) / vol).rolling(3).mean().clip(-1, 1)
        return result
