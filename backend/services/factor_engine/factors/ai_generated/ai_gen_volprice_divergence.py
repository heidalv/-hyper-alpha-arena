"""AI因子: 量价背离 | 置信:55% | 价格短期上涨但成交量萎缩（缩量上涨）通常预示动能不足，反之放量上涨确认趋势。用价格变化方向与成交量变化方向的乘积刻画量价配合度，负向背离给出反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期上涨但成交量萎缩（缩量上涨）通常预示动能不足，反之放量上涨确认趋势。用价格变化方向与成交量变化方向的乘积刻画量价配合度，负向背离给出反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprice_divergence",
            name="Volume-Price Divergence",
            display_name="量价背离",
            description="价格短期上涨但成交量萎缩（缩量上涨）通常预示动能不足，反之放量上涨确认趋势。用价格变化方向与成交量变化方向的乘积刻画量价配合度，负向背离给出反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        pr = data['close'].pct_change(5)
        vr = data['volume'].pct_change(5)
        vstd = data['volume'].pct_change().rolling(20).std()
        result = (pr * (vr / (vstd + 1e-9))).rolling(3).mean().clip(-1, 1)
        return result
