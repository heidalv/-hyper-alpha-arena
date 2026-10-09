"""AI因子: 量价背离因子 | 置信:58% | 价格上行但成交量萎缩（量价背离）往往预示上涨乏力，价格下行但成交量放大则可能恐慌见底。用价格变化方向与成交量变化方向的乘积取负，捕捉量价背离后的反转机会。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上行但成交量萎缩（量价背离）往往预示上涨乏力，价格下行但成交量放大则可能恐慌见底。用价格变化方向与成交量变化方向的乘积取负，捕捉量价背离后的反转机会。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence_20",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="价格上行但成交量萎缩（量价背离）往往预示上涨乏力，价格下行但成交量放大则可能恐慌见底。用价格变化方向与成交量变化方向的乘积取负，捕捉量价背离后的反转机会。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (-(ret * vol_chg) / vol_std).rolling(3).mean().clip(-1, 1)
        return result
