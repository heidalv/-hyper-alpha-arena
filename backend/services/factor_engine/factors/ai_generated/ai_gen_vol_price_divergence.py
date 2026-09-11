"""AI因子: 量价背离 | 置信:50% | 价格变化与成交量变化的背离程度。放量上涨或缩量下跌为健康信号，量价背离则预示反转。用价格收益与成交量变化的相关性方向构造。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格变化与成交量变化的背离程度。放量上涨或缩量下跌为健康信号，量价背离则预示反转。用价格收益与成交量变化的相关性方向构造。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格变化与成交量变化的背离程度。放量上涨或缩量下跌为健康信号，量价背离则预示反转。用价格收益与成交量变化的相关性方向构造。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        volchg = data['volume'].pct_change(5)
        volstd = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (ret * (volchg / volstd)).clip(-1, 1)
        return result
