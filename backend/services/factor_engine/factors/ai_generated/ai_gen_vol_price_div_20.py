"""AI因子: 量价背离20 | 置信:55% | 价格创新高但成交量未同步放大时，上涨动能不足；价格下跌但缩量时抛压减弱。用收益与量变化的滚动相关性反向构造背离因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence20(BaseFactor):
    """价格创新高但成交量未同步放大时，上涨动能不足；价格下跌但缩量时抛压减弱。用收益与量变化的滚动相关性反向构造背离因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div_20",
            name="Volume Price Divergence 20",
            display_name="量价背离20",
            description="价格创新高但成交量未同步放大时，上涨动能不足；价格下跌但缩量时抛压减弱。用收益与量变化的滚动相关性反向构造背离因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change()
        volchg = data['volume'].pct_change()
        corr = ret.rolling(20).corr(volchg)
        result = (-corr).clip(-1, 1)
        return result
