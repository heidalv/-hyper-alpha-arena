"""AI因子: 动量加速度反转 | 置信:60% | 短期动量相对长期动量的加速度过大时，往往出现均值回归。用5日收益减20日收益衡量加速度，再除以波动率标准化，取负号捕捉过热后的反转，输出[-1,1]。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MomentumAccelerationReversal(BaseFactor):
    """短期动量相对长期动量的加速度过大时，往往出现均值回归。用5日收益减20日收益衡量加速度，再除以波动率标准化，取负号捕捉过热后的反转，输出[-1,1]。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_accel_rev",
            name="Momentum Acceleration Reversal",
            display_name="动量加速度反转",
            description="短期动量相对长期动量的加速度过大时，往往出现均值回归。用5日收益减20日收益衡量加速度，再除以波动率标准化，取负号捕捉过热后的反转，输出[-1,1]。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short = data['close'].pct_change(5)
        long = data['close'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std() + 1e-9
        accel = (short - long) / vol
        result = (-accel).clip(-1, 1)
        return result
