"""AI因子: 量价背离 | 置信:58% | 价格短期上涨但成交量相对萎缩时视为背离，预示动能衰竭；价格下跌而缩量则抛压减弱。用量比与收益方向交互构造反转因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期上涨但成交量相对萎缩时视为背离，预示动能衰竭；价格下跌而缩量则抛压减弱。用量比与收益方向交互构造反转因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprice_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期上涨但成交量相对萎缩时视为背离，预示动能衰竭；价格下跌而缩量则抛压减弱。用量比与收益方向交互构造反转因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        volr = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (-1 * ret * (volr - 1)).clip(-1, 1)
        return result
