"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与长期动量之差衡量动量加速度：短周期收益显著强于长周期均值时视为加速上行，反之加速下行。用波动率归一化后截断，捕捉趋势延续与拐点。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAcceleration(BaseFactor):
    """短期动量与长期动量之差衡量动量加速度：短周期收益显著强于长周期均值时视为加速上行，反之加速下行。用波动率归一化后截断，捕捉趋势延续与拐点。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_10",
            name="Multi-Horizon Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与长期动量之差衡量动量加速度：短周期收益显著强于长周期均值时视为加速上行，反之加速下行。用波动率归一化后截断，捕捉趋势延续与拐点。",
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
