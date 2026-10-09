"""AI因子: 量价背离10 | 置信:55% | 价格10日变化与成交量10日变化方向背离时给出反转信号，价涨量缩或价跌量增预示反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence10(BaseFactor):
    """价格10日变化与成交量10日变化方向背离时给出反转信号，价涨量缩或价跌量增预示反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div_10",
            name="Volume Price Divergence 10",
            display_name="量价背离10",
            description="价格10日变化与成交量10日变化方向背离时给出反转信号，价涨量缩或价跌量增预示反转。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        pchg = data['close'].pct_change(10)
        vchg = data['volume'].pct_change(10)
        result = (-(pchg * vchg)).clip(-1, 1)
        return result
