"""AI因子: 多周期动量加速度 | 置信:60% | 短期动量与中期动量之差衡量动量加速度：短期显著强于中期代表趋势加速上行，反之加速下行。以波动率标准化后输出方向性得分。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短期动量与中期动量之差衡量动量加速度：短期显著强于中期代表趋势加速上行，反之加速下行。以波动率标准化后输出方向性得分。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_accel_multiperiod",
            name="Multi-Period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短期动量与中期动量之差衡量动量加速度：短期显著强于中期代表趋势加速上行，反之加速下行。以波动率标准化后输出方向性得分。",
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
