"""AI因子: 收盘位置振幅因子 | 置信:55% | 收盘价在当日高低区间中的相对位置，衡量买方或卖方对当日价格区间的控制力。位置越高说明买方掌控越强，取近5日均值并减去0.5中心化，输出趋势延续信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ClosePositionInRange(BaseFactor):
    """收盘价在当日高低区间中的相对位置，衡量买方或卖方对当日价格区间的控制力。位置越高说明买方掌控越强，取近5日均值并减去0.5中心化，输出趋势延续信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_close_pos_amp",
            name="Close Position in Range",
            display_name="收盘位置振幅因子",
            description="收盘价在当日高低区间中的相对位置，衡量买方或卖方对当日价格区间的控制力。位置越高说明买方掌控越强，取近5日均值并减去0.5中心化，输出趋势延续信号。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        rng = (data['high'] - data['low']).abs() + 1e-9
        pos = (data['close'] - data['low']) / rng
        result = (pos - 0.5).rolling(5).mean().clip(-1, 1)
        return result
