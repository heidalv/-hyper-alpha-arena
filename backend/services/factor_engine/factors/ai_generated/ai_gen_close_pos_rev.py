"""AI因子: 收盘位置反转 | 置信:55% | 收盘价在当日高低区间中的位置反映多空力量：收盘接近最高价说明买方强势但短期可能超买，接近最低价则超卖。用位置偏离中值的程度构造短期反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionReversal(BaseFactor):
    """收盘价在当日高低区间中的位置反映多空力量：收盘接近最高价说明买方强势但短期可能超买，接近最低价则超卖。用位置偏离中值的程度构造短期反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_pos_rev",
            name="Close Position Reversal",
            display_name="收盘位置反转",
            description="收盘价在当日高低区间中的位置反映多空力量：收盘接近最高价说明买方强势但短期可能超买，接近最低价则超卖。用位置偏离中值的程度构造短期反转。",
            category="technical",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']) + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (-(pos - 0.5) * 2).rolling(3).mean().clip(-1, 1)
        return result
