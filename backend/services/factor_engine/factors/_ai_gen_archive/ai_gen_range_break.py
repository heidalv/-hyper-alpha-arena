"""AI因子: 区间突破动量 | 置信:55% | 从SL亏损和max_hold_timeout亏损看，止损单在假突破中频繁触发。该因子捕捉真实突破动量，当价格突破近期区间且伴随放量时给予强信号，避免在无趋势震荡中持仓超时。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Rangebreakmomentum(BaseFactor):
    """从SL亏损和max_hold_timeout亏损看，止损单在假突破中频繁触发。该因子捕捉真实突破动量，当价格突破近期区间且伴随放量时给予强信号，避免在无趋势震荡中持仓超时。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_range_break",
            name="RangeBreakMomentum",
            display_name="区间突破动量",
            description="从SL亏损和max_hold_timeout亏损看，止损单在假突破中频繁触发。该因子捕捉真实突破动量，当价格突破近期区间且伴随放量时给予强信号，避免在无趋势震荡中持仓超时。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high'].rolling(20).max()
        low = data['low'].rolling(20).min()
        mid = (high + low) / 2
        pos = (data['close'] - mid) / (high - low + 1e-9)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        result = (pos * vol_ratio).clip(-1, 1)
        return result
