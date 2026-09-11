"""AI因子: 量价背离因子 | 置信:55% | 价格上行但成交量萎缩(量价背离)预示动能衰竭，价格下行但缩量则抛压减轻。用价格动量与成交量动量的标准化差刻画背离强度，反向预测未来收益。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上行但成交量萎缩(量价背离)预示动能衰竭，价格下行但缩量则抛压减轻。用价格动量与成交量动量的标准化差刻画背离强度，反向预测未来收益。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="价格上行但成交量萎缩(量价背离)预示动能衰竭，价格下行但缩量则抛压减轻。用价格动量与成交量动量的标准化差刻画背离强度，反向预测未来收益。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vret = data['volume'].pct_change(5)
        vstd = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (-(ret - vret / vstd)).clip(-1, 1)
        return result
