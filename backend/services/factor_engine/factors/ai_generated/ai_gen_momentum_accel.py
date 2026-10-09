"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与中期动量之差，衡量动量加速度，正值表示上涨动能增强，负值表示动能衰减，作为趋势延续/反转的alpha信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期动量与中期动量之差，衡量动量加速度，正值表示上涨动能增强，负值表示动能衰减，作为趋势延续/反转的alpha信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel",
            name="Multi-Period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与中期动量之差，衡量动量加速度，正值表示上涨动能增强，负值表示动能衰减，作为趋势延续/反转的alpha信号。",
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
