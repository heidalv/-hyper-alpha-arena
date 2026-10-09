"""AI因子: 收盘位置强度 | 置信:57% | 收盘价在当日高低区间中的相对位置，接近高点表示买盘强势，接近低点表示卖压主导。用滚动均值平滑后作为短线方向因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionStrength(BaseFactor):
    """收盘价在当日高低区间中的相对位置，接近高点表示买盘强势，接近低点表示卖压主导。用滚动均值平滑后作为短线方向因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_position",
            name="Close Position Strength",
            display_name="收盘位置强度",
            description="收盘价在当日高低区间中的相对位置，接近高点表示买盘强势，接近低点表示卖压主导。用滚动均值平滑后作为短线方向因子。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (pos.rolling(5).mean() - 0.5).clip(-1, 1)
        return result
