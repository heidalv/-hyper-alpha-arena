"""AI因子: 多周期动量加速度反转 | 置信:60% | 短期动量与长期动量之差衡量动量加速度。当短期收益显著弱于长期收益（加速度为负）时，往往出现超卖反弹；反之超买回落。用波动率归一化后取负号，捕捉动量加速度的均值回归特性，因子值越高表示超卖反弹概率越大。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiHorizonMomentumAccelerationReversal(BaseFactor):
    """短期动量与长期动量之差衡量动量加速度。当短期收益显著弱于长期收益（加速度为负）时，往往出现超卖反弹；反之超买回落。用波动率归一化后取负号，捕捉动量加速度的均值回归特性，因子值越高表示超卖反弹概率越大。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_rev",
            name="Multi-Horizon Momentum Acceleration Reversal",
            display_name="多周期动量加速度反转",
            description="短期动量与长期动量之差衡量动量加速度。当短期收益显著弱于长期收益（加速度为负）时，往往出现超卖反弹；反之超买回落。用波动率归一化后取负号，捕捉动量加速度的均值回归特性，因子值越高表示超卖反弹概率越大。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        accel = (short - long) / (vol + 1e-9)
        result = (-accel).clip(-1, 1)
        return result
