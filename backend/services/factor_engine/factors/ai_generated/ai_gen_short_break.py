"""AI因子: 空头突破量能确认 | 置信:65% | 捕捉空头趋势中价格跌破关键支撑且伴随放量的情形，利用收盘价相对近期低点的位置与成交量放大程度构建因子，值越大表示空头动能越强"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortBreakdownVolumeConfirm(BaseFactor):
    """捕捉空头趋势中价格跌破关键支撑且伴随放量的情形，利用收盘价相对近期低点的位置与成交量放大程度构建因子，值越大表示空头动能越强"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_break",
            name="Short_breakdown_volume_confirm",
            display_name="空头突破量能确认",
            description="捕捉空头趋势中价格跌破关键支撑且伴随放量的情形，利用收盘价相对近期低点的位置与成交量放大程度构建因子，值越大表示空头动能越强",
            category="technical",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        low_min = data['low'].rolling(20).min()
        close_break = (data['close'] - low_min) / (data['close'].rolling(20).max() - low_min + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (-close_break * vol_ratio).clip(-1, 1)
        return result
