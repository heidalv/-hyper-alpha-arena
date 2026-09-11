"""AI因子: 量价背离反转 | 置信:55% | 当价格创新低但成交量萎缩时，表明抛压减弱，可能反转向上；当价格创新高但成交量萎缩时，表明上涨动力不足，可能反转向下。通过价格位置与成交量变化的背离构造因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergenceReversal(BaseFactor):
    """当价格创新低但成交量萎缩时，表明抛压减弱，可能反转向上；当价格创新高但成交量萎缩时，表明上涨动力不足，可能反转向下。通过价格位置与成交量变化的背离构造因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_002",
            name="Volume_Price_Divergence_Reversal",
            display_name="量价背离反转",
            description="当价格创新低但成交量萎缩时，表明抛压减弱，可能反转向上；当价格创新高但成交量萎缩时，表明上涨动力不足，可能反转向下。通过价格位置与成交量变化的背离构造因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        vol = data['volume']
        price_pos = (close - close.rolling(20).min()) / (close.rolling(20).max() - close.rolling(20).min() + 1e-9)
        vol_ratio = vol / vol.rolling(20).mean()
        result = ((price_pos - 0.5) * (1 - vol_ratio)).clip(-1, 1)
        return result
