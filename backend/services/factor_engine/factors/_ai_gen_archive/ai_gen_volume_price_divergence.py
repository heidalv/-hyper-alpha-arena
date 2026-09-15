"""AI因子: 量价背离 | 置信:55% | 价格短期涨幅与成交量变化方向背离时提示反转：价升量缩(背离)倾向回落，价跌量缩(抛压衰竭)倾向反弹。用价格动量与量能变化的差值构造。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化方向背离时提示反转：价升量缩(背离)倾向回落，价跌量缩(抛压衰竭)倾向反弹。用价格动量与量能变化的差值构造。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volume_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期涨幅与成交量变化方向背离时提示反转：价升量缩(背离)倾向回落，价跌量缩(抛压衰竭)倾向反弹。用价格动量与量能变化的差值构造。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        price_mom = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_base = data['volume'].rolling(20).mean() + 1e-9
        result = (price_mom - (vol_chg * data['volume'] / vol_base) * 0.1).clip(-1, 1)
        return result
