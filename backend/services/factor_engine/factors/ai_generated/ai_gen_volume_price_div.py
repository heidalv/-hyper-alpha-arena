"""AI因子: 量价背离 | 置信:55% | 价格5日变化与成交量5日变化的符号背离：价格涨但量缩(或价跌量增)时给出反向信号，捕捉量价背离后的反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格5日变化与成交量5日变化的符号背离：价格涨但量缩(或价跌量增)时给出反向信号，捕捉量价背离后的反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格5日变化与成交量5日变化的符号背离：价格涨但量缩(或价跌量增)时给出反向信号，捕捉量价背离后的反转。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        pchg = data['close'].pct_change(5)
        vchg = data['volume'].pct_change(5)
        result = (-pchg * vchg).clip(-1, 1)
        return result
