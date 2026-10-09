"""AI因子: 振幅位置均值回归 | 置信:57% | 收盘价在近期高低区间中的相对位置，接近区间顶部表示超买、接近底部表示超卖。取负向偏离度作为反转因子，因子值高(接近底部)预示未来上涨概率高。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RangePositionMeanReversion(BaseFactor):
    """收盘价在近期高低区间中的相对位置，接近区间顶部表示超买、接近底部表示超卖。取负向偏离度作为反转因子，因子值高(接近底部)预示未来上涨概率高。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_range_position_reversion",
            name="Range Position Mean Reversion",
            display_name="振幅位置均值回归",
            description="收盘价在近期高低区间中的相对位置，接近区间顶部表示超买、接近底部表示超卖。取负向偏离度作为反转因子，因子值高(接近底部)预示未来上涨概率高。",
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
