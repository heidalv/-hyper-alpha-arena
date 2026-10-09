"""AI因子: 波动归一化动量加速度 | 置信:58% | 用5期与20期收益率之差衡量动量加速度，并以20期已实现波动率归一化，捕捉趋势加速阶段的持续性alpha，波动归一化避免高波动品种主导。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationVolNormalized(BaseFactor):
    """用5期与20期收益率之差衡量动量加速度，并以20期已实现波动率归一化，捕捉趋势加速阶段的持续性alpha，波动归一化避免高波动品种主导。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_volnorm",
            name="Momentum Acceleration Vol Normalized",
            display_name="波动归一化动量加速度",
            description="用5期与20期收益率之差衡量动量加速度，并以20期已实现波动率归一化，捕捉趋势加速阶段的持续性alpha，波动归一化避免高波动品种主导。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret5 = data['close'].pct_change(5)
        ret20 = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((ret5 - ret20) / (vol * np.sqrt(5) + 1e-9)).clip(-1, 1)
        return result
