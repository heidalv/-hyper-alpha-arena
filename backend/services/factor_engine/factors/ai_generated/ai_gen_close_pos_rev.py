"""AI因子: 收盘位置均值回归 | 置信:57% | 收盘价在近期高低区间中的相对位置。位置过高（接近上轨）预示短期超买回落，位置过低预示超卖反弹，取负向作为反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionMeanReversion(BaseFactor):
    """收盘价在近期高低区间中的相对位置。位置过高（接近上轨）预示短期超买回落，位置过低预示超卖反弹，取负向作为反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_pos_rev",
            name="Close Position Mean Reversion",
            display_name="收盘位置均值回归",
            description="收盘价在近期高低区间中的相对位置。位置过高（接近上轨）预示短期超买回落，位置过低预示超卖反弹，取负向作为反转因子。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        hh = data['high'].rolling(20).max()
        ll = data['low'].rolling(20).min()
        pos = (data['close'] - ll) / (hh - ll + 1e-9)
        result = (0.5 - pos).clip(-1, 1)
        return result
