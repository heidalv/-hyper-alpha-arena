"""AI因子: 量价背离 | 置信:55% | 价格短期上涨但成交量相对萎缩（量价背离）往往预示动能衰竭，未来回落概率高；价格下跌而放量则可能接近底部。用短周期收益与成交量相对中位数的偏离度做交互，输出方向性因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期上涨但成交量相对萎缩（量价背离）往往预示动能衰竭，未来回落概率高；价格下跌而放量则可能接近底部。用短周期收益与成交量相对中位数的偏离度做交互，输出方向性因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期上涨但成交量相对萎缩（量价背离）往往预示动能衰竭，未来回落概率高；价格下跌而放量则可能接近底部。用短周期收益与成交量相对中位数的偏离度做交互，输出方向性因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).median() + 1e-9)
        result = (ret.rank(pct=True) * 2 - 1) * (1 - vol_ratio.rank(pct=True) * 2)
        result = result.clip(-1, 1)
        return result
