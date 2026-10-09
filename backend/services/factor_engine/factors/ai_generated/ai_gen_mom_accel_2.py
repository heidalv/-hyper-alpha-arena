"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与长期动量之差衡量动量加速度。短期动量强于长期动量说明趋势正在加速，未来上涨概率更高；反之则趋势衰竭。用波动率归一化后输出方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期动量与长期动量之差衡量动量加速度。短期动量强于长期动量说明趋势正在加速，未来上涨概率更高；反之则趋势衰竭。用波动率归一化后输出方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_2",
            name="Multi-Period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与长期动量之差衡量动量加速度。短期动量强于长期动量说明趋势正在加速，未来上涨概率更高；反之则趋势衰竭。用波动率归一化后输出方向信号。",
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
