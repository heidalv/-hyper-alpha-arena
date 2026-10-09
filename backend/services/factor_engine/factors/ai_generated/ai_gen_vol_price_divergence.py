"""AI因子: 量价背离 | 置信:58% | 当价格上涨但成交量相对萎缩时，视为上涨乏力信号；价格下跌但成交量放大时视为恐慌抛售可能反转。用收益方向与量能变化的背离构造因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """当价格上涨但成交量相对萎缩时，视为上涨乏力信号；价格下跌但成交量放大时视为恐慌抛售可能反转。用收益方向与量能变化的背离构造因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="当价格上涨但成交量相对萎缩时，视为上涨乏力信号；价格下跌但成交量放大时视为恐慌抛售可能反转。用收益方向与量能变化的背离构造因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (-ret * vol_chg / vol_std).clip(-1, 1)
        return result
