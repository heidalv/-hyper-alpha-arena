"""AI因子: 量价背离 | 置信:50% | 价格短期上涨但成交量萎缩(量价背离)预示上涨乏力；价格下跌但缩量预示抛压减弱。用价格变动方向与成交量变化的背离度构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期上涨但成交量萎缩(量价背离)预示上涨乏力；价格下跌但缩量预示抛压减弱。用价格变动方向与成交量变化的背离度构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期上涨但成交量萎缩(量价背离)预示上涨乏力；价格下跌但缩量预示抛压减弱。用价格变动方向与成交量变化的背离度构造反转因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (-(ret * (vol_chg / vol_std))).clip(-1, 1)
        return result
