"""AI因子: 动量加速度 | 置信:55% | 多周期动量差：短期收益减去中期收益，正值表示动量加速上行，负值表示动量衰减或反转。捕捉趋势延续与拐点信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """多周期动量差：短期收益减去中期收益，正值表示动量加速上行，负值表示动量衰减或反转。捕捉趋势延续与拐点信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="多周期动量差：短期收益减去中期收益，正值表示动量加速上行，负值表示动量衰减或反转。捕捉趋势延续与拐点信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((fast - slow) / vol).rolling(3).mean().clip(-1, 1)
        return result
