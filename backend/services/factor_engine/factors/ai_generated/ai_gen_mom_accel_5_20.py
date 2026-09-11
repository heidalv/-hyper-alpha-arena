"""AI因子: 动量加速度5对20 | 置信:60% | 短期动量与中期动量之差刻画动量加速度：短期收益强于中期收益时，趋势加速上行概率高；反之则加速下行。用波动率归一化后压缩到[-1,1]，捕捉趋势加速的alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAcceleration5v20(BaseFactor):
    """短期动量与中期动量之差刻画动量加速度：短期收益强于中期收益时，趋势加速上行概率高；反之则加速下行。用波动率归一化后压缩到[-1,1]，捕捉趋势加速的alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_5_20",
            name="Momentum Acceleration 5v20",
            display_name="动量加速度5对20",
            description="短期动量与中期动量之差刻画动量加速度：短期收益强于中期收益时，趋势加速上行概率高；反之则加速下行。用波动率归一化后压缩到[-1,1]，捕捉趋势加速的alpha。",
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
