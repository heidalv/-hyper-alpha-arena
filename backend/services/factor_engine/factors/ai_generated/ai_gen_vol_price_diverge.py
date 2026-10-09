"""AI因子: 量价背离 | 置信:55% | 当价格短期上涨但成交量相对均量萎缩时视为上涨动能不足，反之价格下跌但缩量视为抛压衰竭，构造量价背离反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """当价格短期上涨但成交量相对均量萎缩时视为上涨动能不足，反之价格下跌但缩量视为抛压衰竭，构造量价背离反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_diverge",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="当价格短期上涨但成交量相对均量萎缩时视为上涨动能不足，反之价格下跌但缩量视为抛压衰竭，构造量价背离反转因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'].rolling(5).mean() / (data['volume'].rolling(30).mean() + 1e-9)
        result = (-ret * (1.0 - vol_ratio)).clip(-1, 1)
        return result
