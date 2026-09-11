"""AI因子: 量价背离 | 置信:58% | 当价格上涨但成交量相对萎缩时，说明上涨动能不足，未来回落概率高；价格下跌但成交量放大时，抛压释放后可能反弹。用价格变化方向与成交量相对均值的偏离做交互，捕捉量价背离alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """当价格上涨但成交量相对萎缩时，说明上涨动能不足，未来回落概率高；价格下跌但成交量放大时，抛压释放后可能反弹。用价格变化方向与成交量相对均值的偏离做交互，捕捉量价背离alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="当价格上涨但成交量相对萎缩时，说明上涨动能不足，未来回落概率高；价格下跌但成交量放大时，抛压释放后可能反弹。用价格变化方向与成交量相对均值的偏离做交互，捕捉量价背离alpha。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = (ret * (1 - vol_ratio)).rolling(3).mean().clip(-1, 1)
        return result
