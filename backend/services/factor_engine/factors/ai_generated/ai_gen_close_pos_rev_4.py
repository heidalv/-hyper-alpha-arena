"""AI因子: 收盘位置均值回归 | 置信:58% | 收盘价在当日高低区间中的相对位置反映买卖压力。连续处于高位(接近1)说明超买，未来回落概率大；连续处于低位说明超卖，未来反弹概率大。用多期均值平滑后取反。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionMeanReversion(BaseFactor):
    """收盘价在当日高低区间中的相对位置反映买卖压力。连续处于高位(接近1)说明超买，未来回落概率大；连续处于低位说明超卖，未来反弹概率大。用多期均值平滑后取反。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_pos_rev_4",
            name="Close Position Mean Reversion",
            display_name="收盘位置均值回归",
            description="收盘价在当日高低区间中的相对位置反映买卖压力。连续处于高位(接近1)说明超买，未来回落概率大；连续处于低位说明超卖，未来反弹概率大。用多期均值平滑后取反。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (-(pos.rolling(5).mean() - 0.5) * 2).clip(-1, 1)
        return result
