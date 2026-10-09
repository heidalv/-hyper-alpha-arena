"""AI因子: 收盘位置反转 | 置信:57% | 收盘价在当日高低区间中的相对位置反映买卖方力量对比。连续多日收盘位置偏高(超买)后回落概率大，偏低(超卖)后反弹概率大，用收盘位置的滚动均值构造反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionReversal(BaseFactor):
    """收盘价在当日高低区间中的相对位置反映买卖方力量对比。连续多日收盘位置偏高(超买)后回落概率大，偏低(超卖)后反弹概率大，用收盘位置的滚动均值构造反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_position_rev",
            name="Close Position Reversal",
            display_name="收盘位置反转",
            description="收盘价在当日高低区间中的相对位置反映买卖方力量对比。连续多日收盘位置偏高(超买)后回落概率大，偏低(超卖)后反弹概率大，用收盘位置的滚动均值构造反转信号。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (-(pos - 0.5) * 2).rolling(5).mean().clip(-1, 1)
        return result
