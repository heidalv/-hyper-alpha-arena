"""AI因子: 量价背离因子 | 置信:55% | 价格创新高但成交量未同步放大时，上涨动能不足，未来回落概率高；价格下跌但缩量时抛压减弱，未来反弹概率高。用价格动量与成交量动量的差值衡量背离程度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格创新高但成交量未同步放大时，上涨动能不足，未来回落概率高；价格下跌但缩量时抛压减弱，未来反弹概率高。用价格动量与成交量动量的差值衡量背离程度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="价格创新高但成交量未同步放大时，上涨动能不足，未来回落概率高；价格下跌但缩量时抛压减弱，未来反弹概率高。用价格动量与成交量动量的差值衡量背离程度。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_mom = data['close'].pct_change(10)
        vol_mom = data['volume'].pct_change(10)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = ((price_mom - vol_mom / vol_std) * -1).clip(-1, 1)
        return result
