"""AI因子: 量价背离因子 | 置信:55% | 当价格上涨但成交量相对萎缩，或价格下跌但成交量放大时，量价背离出现，往往预示反转。用价格变化与成交量变化的滚动相关性取负，作为反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """当价格上涨但成交量相对萎缩，或价格下跌但成交量放大时，量价背离出现，往往预示反转。用价格变化与成交量变化的滚动相关性取负，作为反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_diverge",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="当价格上涨但成交量相对萎缩，或价格下跌但成交量放大时，量价背离出现，往往预示反转。用价格变化与成交量变化的滚动相关性取负，作为反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        volchg = data['volume'].pct_change()
        corr = ret.rolling(10).corr(volchg)
        result = (-corr).clip(-1, 1)
        return result
