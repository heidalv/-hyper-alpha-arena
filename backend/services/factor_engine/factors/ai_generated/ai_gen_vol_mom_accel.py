"""AI因子: 波动调整动量加速度 | 置信:60% | 用5期与20期收益率之差衡量动量加速度，并以20期已实现波动率归一化，捕捉趋势加速阶段的延续性alpha，同时抑制高波动噪声。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """用5期与20期收益率之差衡量动量加速度，并以20期已实现波动率归一化，捕捉趋势加速阶段的延续性alpha，同时抑制高波动噪声。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_mom_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="用5期与20期收益率之差衡量动量加速度，并以20期已实现波动率归一化，捕捉趋势加速阶段的延续性alpha，同时抑制高波动噪声。",
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
