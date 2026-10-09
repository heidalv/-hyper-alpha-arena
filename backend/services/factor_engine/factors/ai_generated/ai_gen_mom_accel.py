"""AI因子: 动量加速度 | 置信:60% | 多周期动量差：短期收益率减去长期收益率，捕捉动量加速或衰减。正值表示近期动能强于中期，趋势延续概率高；负值表示动能衰竭，可能反转。用波动率归一化后截断到[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration(BaseFactor):
    """多周期动量差：短期收益率减去长期收益率，捕捉动量加速或衰减。正值表示近期动能强于中期，趋势延续概率高；负值表示动能衰竭，可能反转。用波动率归一化后截断到[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel",
            name="Momentum Acceleration",
            display_name="动量加速度",
            description="多周期动量差：短期收益率减去长期收益率，捕捉动量加速或衰减。正值表示近期动能强于中期，趋势延续概率高；负值表示动能衰竭，可能反转。用波动率归一化后截断到[-1,1]。",
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
