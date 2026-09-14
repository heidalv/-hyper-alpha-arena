"""AI因子: 量价背离20 | 置信:55% | 价格20日变化与成交量20日变化方向背离时，往往意味着趋势动能不足。当价格上涨但成交量萎缩（量价背离）时，未来回落概率上升；价格下跌但放量则可能见底。取负号使因子值与未来收益方向一致。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence20(BaseFactor):
    """价格20日变化与成交量20日变化方向背离时，往往意味着趋势动能不足。当价格上涨但成交量萎缩（量价背离）时，未来回落概率上升；价格下跌但放量则可能见底。取负号使因子值与未来收益方向一致。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence_20",
            name="Volume Price Divergence 20",
            display_name="量价背离20",
            description="价格20日变化与成交量20日变化方向背离时，往往意味着趋势动能不足。当价格上涨但成交量萎缩（量价背离）时，未来回落概率上升；价格下跌但放量则可能见底。取负号使因子值与未来收益方向一致。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        pr = data['close'].pct_change(20)
        vr = data['volume'].pct_change(20)
        vol = data['close'].pct_change().rolling(20).std()
        result = (-(pr * vr) / (vol + 1e-9)).clip(-1, 1)
        return result
