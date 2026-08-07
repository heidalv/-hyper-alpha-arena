"""AI因子: 趋势疲劳度 | 置信:65% | 衡量价格偏离均线的加速度是否衰减。当价格远离均线但动能减弱时，趋势可能衰竭，返回负值，预示应避免追单或提前平仓，以规避hold_timeout和running_close亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class TrendFatigueIndicator(BaseFactor):
    """衡量价格偏离均线的加速度是否衰减。当价格远离均线但动能减弱时，趋势可能衰竭，返回负值，预示应避免追单或提前平仓，以规避hold_timeout和running_close亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_tf",
            name="Trend Fatigue Indicator",
            display_name="趋势疲劳度",
            description="衡量价格偏离均线的加速度是否衰减。当价格远离均线但动能减弱时，趋势可能衰竭，返回负值，预示应避免追单或提前平仓，以规避hold_timeout和running_close亏损。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        ema20 = close.ewm(span=20, adjust=False).mean()
        distance = (close - ema20) / ema20
        accel = distance.diff(5)
        norm_accel = accel / (accel.abs().rolling(50).mean() + 1e-8)
        result = norm_accel.clip(-2, 2) / 2
        result = result.fillna(0).clip(-1, 1)
        return result
