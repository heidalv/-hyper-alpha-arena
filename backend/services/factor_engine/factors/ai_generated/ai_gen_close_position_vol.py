"""AI因子: 量价收盘位置 | 置信:57% | 收盘价在当日高低区间中的位置（0=最低，1=最高）减去0.5，乘以成交量相对20日均量的放大倍数，衡量放量突破确认。收盘位置高且放量→买方强势，预期上涨。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionInRangeWithVolume(BaseFactor):
    """收盘价在当日高低区间中的位置（0=最低，1=最高）减去0.5，乘以成交量相对20日均量的放大倍数，衡量放量突破确认。收盘位置高且放量→买方强势，预期上涨。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_position_vol",
            name="Close Position in Range with Volume",
            display_name="量价收盘位置",
            description="收盘价在当日高低区间中的位置（0=最低，1=最高）减去0.5，乘以成交量相对20日均量的放大倍数，衡量放量突破确认。收盘位置高且放量→买方强势，预期上涨。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = data['high'] - data['low'] + 1e-9
        pos = (data['close'] - data['low']) / rng - 0.5
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (pos * vol_ratio).clip(-1, 1)
        return result
