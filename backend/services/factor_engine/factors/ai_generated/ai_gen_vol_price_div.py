"""AI因子: 量价背离 | 置信:58% | 价格上行但成交量萎缩（或价格下行但成交量放大）时，趋势动能不足，预期反转；用收益与量变化的符号背离构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上行但成交量萎缩（或价格下行但成交量放大）时，趋势动能不足，预期反转；用收益与量变化的符号背离构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格上行但成交量萎缩（或价格下行但成交量放大）时，趋势动能不足，预期反转；用收益与量变化的符号背离构造反转因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        volchg = data['volume'].pct_change(5)
        volstd = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (-(ret * volchg) / (volstd * 10)).clip(-1, 1)
        return result
