"""AI因子: 量价背离 | 置信:58% | 价格短期上涨但成交量萎缩（缩量上涨）往往预示动能不足，未来回落概率高；价格下跌但放量则可能见底。构造价格变化方向与成交量变化方向的背离度，捕捉量价关系中的反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期上涨但成交量萎缩（缩量上涨）往往预示动能不足，未来回落概率高；价格下跌但放量则可能见底。构造价格变化方向与成交量变化方向的背离度，捕捉量价关系中的反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期上涨但成交量萎缩（缩量上涨）往往预示动能不足，未来回落概率高；价格下跌但放量则可能见底。构造价格变化方向与成交量变化方向的背离度，捕捉量价关系中的反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_chg = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_ma = data['volume'].rolling(20).mean() + 1e-9
        norm_vol = data['volume'] / vol_ma
        result = ((price_chg * (1 - norm_vol)) / (data['close'].pct_change().rolling(20).std() + 1e-9)).clip(-1, 1)
        return result
