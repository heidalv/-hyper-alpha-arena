"""AI因子: 量价背离因子 | 置信:58% | 当价格创新高但成交量未同步放大时，上涨动能衰竭，未来回落概率高；当价格下跌但缩量时，抛压减弱，反弹概率高。用价格动量与成交量动量的差值衡量背离强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """当价格创新高但成交量未同步放大时，上涨动能衰竭，未来回落概率高；当价格下跌但缩量时，抛压减弱，反弹概率高。用价格动量与成交量动量的差值衡量背离强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence_30",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="当价格创新高但成交量未同步放大时，上涨动能衰竭，未来回落概率高；当价格下跌但缩量时，抛压减弱，反弹概率高。用价格动量与成交量动量的差值衡量背离强度。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_mom = data['close'].pct_change(10)
        vol_mom = data['volume'].pct_change(10)
        vol_norm = data['volume'].rolling(30).mean()
        result = ((price_mom - vol_mom) / (data['close'].pct_change().rolling(20).std() + 1e-9)).clip(-1, 1)
        return result
