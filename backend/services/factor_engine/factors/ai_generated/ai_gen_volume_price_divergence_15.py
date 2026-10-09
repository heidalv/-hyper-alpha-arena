"""AI因子: 量价背离 | 置信:55% | 价格短期涨幅与成交量变化的背离度。价格上涨但成交量萎缩(缩量上涨)预示动能不足，价格下跌但放量(恐慌抛售)可能见底。用价格收益与成交量变化的滚动相关性取负，捕捉量价背离反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化的背离度。价格上涨但成交量萎缩(缩量上涨)预示动能不足，价格下跌但放量(恐慌抛售)可能见底。用价格收益与成交量变化的滚动相关性取负，捕捉量价背离反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_divergence_15",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期涨幅与成交量变化的背离度。价格上涨但成交量萎缩(缩量上涨)预示动能不足，价格下跌但放量(恐慌抛售)可能见底。用价格收益与成交量变化的滚动相关性取负，捕捉量价背离反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        corr = (ret * vol_chg).rolling(15).mean()
        vol_std = data['close'].pct_change().rolling(15).std() + 1e-9
        result = (-corr / vol_std).clip(-1, 1)
        return result
