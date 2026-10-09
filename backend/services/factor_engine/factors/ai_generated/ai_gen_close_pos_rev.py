"""AI因子: 收盘位置反转 | 置信:60% | 以收盘价在当日高低区间中的相对位置衡量多空力量对比，收盘位置过高代表短期超买、过低代表超卖。对该位置做短期平滑并取反向，捕捉价格向区间中值回归的方向性 alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionReversal(BaseFactor):
    """以收盘价在当日高低区间中的相对位置衡量多空力量对比，收盘位置过高代表短期超买、过低代表超卖。对该位置做短期平滑并取反向，捕捉价格向区间中值回归的方向性 alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_pos_rev",
            name="Close Position Reversal",
            display_name="收盘位置反转",
            description="以收盘价在当日高低区间中的相对位置衡量多空力量对比，收盘位置过高代表短期超买、过低代表超卖。对该位置做短期平滑并取反向，捕捉价格向区间中值回归的方向性 alpha。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (0.5 - pos).rolling(5).mean().clip(-1, 1)
        return result
