"""AI因子: 短期动量加速度 | 置信:60% | 用5日收益与20日收益之差衡量动量加速度：短期动量强于长期动量时因子为正，预示趋势加速上行；反之预示动量衰减或反转。捕捉趋势启动与衰竭的早期信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortMomentumAcceleration(BaseFactor):
    """用5日收益与20日收益之差衡量动量加速度：短期动量强于长期动量时因子为正，预示趋势加速上行；反之预示动量衰减或反转。捕捉趋势启动与衰竭的早期信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_short",
            name="Short Momentum Acceleration",
            display_name="短期动量加速度",
            description="用5日收益与20日收益之差衡量动量加速度：短期动量强于长期动量时因子为正，预示趋势加速上行；反之预示动量衰减或反转。捕捉趋势启动与衰竭的早期信号。",
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
