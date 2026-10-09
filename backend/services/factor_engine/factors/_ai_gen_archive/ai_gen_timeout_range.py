"""AI因子: 超时区间收缩 | 置信:55% | 针对max_hold_timeout且方向不明确的亏损：当价格在近期区间内反复震荡（区间收缩）时，持仓超时容易亏损。该因子用ATR与价格区间宽度比值，区间越窄越给出负值（看空），区间突破时给出正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeoutrangecontraction(BaseFactor):
    """针对max_hold_timeout且方向不明确的亏损：当价格在近期区间内反复震荡（区间收缩）时，持仓超时容易亏损。该因子用ATR与价格区间宽度比值，区间越窄越给出负值（看空），区间突破时给出正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_range",
            name="TimeoutRangeContraction",
            display_name="超时区间收缩",
            description="针对max_hold_timeout且方向不明确的亏损：当价格在近期区间内反复震荡（区间收缩）时，持仓超时容易亏损。该因子用ATR与价格区间宽度比值，区间越窄越给出负值（看空），区间突破时给出正值。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high'].rolling(20).max()
        low = data['low'].rolling(20).min()
        range_width = high - low
        atr = (data['high'] - data['low']).rolling(14).mean()
        ratio = atr / (range_width + 1e-9)
        result = (ratio - 0.5).clip(-1, 1)
        return result
