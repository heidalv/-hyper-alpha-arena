"""AI因子: 波动调整动量加速度 | 置信:60% | 短期(5日)与中期(20日)收益率之差衡量动量加速度，再除以20日已实现波动率做风险归一化。高值代表近期动量相对中期加速且波动可控，预示趋势延续。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolatilityAdjustedMomentumAcceleration(BaseFactor):
    """短期(5日)与中期(20日)收益率之差衡量动量加速度，再除以20日已实现波动率做风险归一化。高值代表近期动量相对中期加速且波动可控，预示趋势延续。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_voladj_momentum_accel",
            name="Volatility Adjusted Momentum Acceleration",
            display_name="波动调整动量加速度",
            description="短期(5日)与中期(20日)收益率之差衡量动量加速度，再除以20日已实现波动率做风险归一化。高值代表近期动量相对中期加速且波动可控，预示趋势延续。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret5 = data['close'].pct_change(5)
        ret20 = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        result = ((ret5 - ret20) / vol).clip(-1, 1)
        return result
