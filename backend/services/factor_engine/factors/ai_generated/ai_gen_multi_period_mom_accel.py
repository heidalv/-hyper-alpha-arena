"""AI因子: 多周期动量加速度 | 置信:60% | 短周期收益与长周期收益之差衡量动量加速度：短期跑赢长期说明趋势正在加速，未来延续概率高；反之则动量衰竭。以波动率归一化后输出方向信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiPeriodMomentumAcceleration(BaseFactor):
    """短周期收益与长周期收益之差衡量动量加速度：短期跑赢长期说明趋势正在加速，未来延续概率高；反之则动量衰竭。以波动率归一化后输出方向信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_multi_period_mom_accel",
            name="Multi Period Momentum Acceleration",
            display_name="多周期动量加速度",
            description="短周期收益与长周期收益之差衡量动量加速度：短期跑赢长期说明趋势正在加速，未来延续概率高；反之则动量衰竭。以波动率归一化后输出方向信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        fast = data['close'].pct_change(5)
        slow = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = ((fast - slow) / (vol + 1e-9)).rolling(3).mean().clip(-1, 1)
        return result
