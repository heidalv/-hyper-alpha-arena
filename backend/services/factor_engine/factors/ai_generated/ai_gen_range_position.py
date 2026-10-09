"""AI因子: 区间位置反转 | 置信:57% | 收盘价在近期高低区间中的相对位置。位于区间顶部(超买)预示回落，位于底部(超卖)预示反弹，是经典均值回归微观结构代理。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangePositionReversion(BaseFactor):
    """收盘价在近期高低区间中的相对位置。位于区间顶部(超买)预示回落，位于底部(超卖)预示反弹，是经典均值回归微观结构代理。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_range_position",
            name="Range Position Reversion",
            display_name="区间位置反转",
            description="收盘价在近期高低区间中的相对位置。位于区间顶部(超买)预示回落，位于底部(超卖)预示反弹，是经典均值回归微观结构代理。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        hi = data['high'].rolling(20).max()
        lo = data['low'].rolling(20).min()
        pos = (data['close'] - lo) / (hi - lo + 1e-9)
        result = (0.5 - pos).clip(-1, 1)
        return result
