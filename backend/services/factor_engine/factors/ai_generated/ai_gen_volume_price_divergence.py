"""AI因子: 量价背离 | 置信:55% | 价格短期涨幅与成交量变化的背离程度，价格上行但量能萎缩(负背离)预示上涨乏力，价格下行但量能放大预示抛压释放，捕捉量价背离反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化的背离程度，价格上行但量能萎缩(负背离)预示上涨乏力，价格下行但量能放大预示抛压释放，捕捉量价背离反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期涨幅与成交量变化的背离程度，价格上行但量能萎缩(负背离)预示上涨乏力，价格下行但量能放大预示抛压释放，捕捉量价背离反转。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = ((ret - vol_chg / vol_std) * -1.0).clip(-1, 1)
        return result
