"""AI因子: 量价背离因子 | 置信:55% | 短期收益方向与成交量变化的背离程度：当价格上涨但成交量萎缩（或下跌但放量）时，趋势可持续性存疑，存在反转机会。用收益符号与量能z-score的乘积衡量背离强度，负值预示反转。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """短期收益方向与成交量变化的背离程度：当价格上涨但成交量萎缩（或下跌但放量）时，趋势可持续性存疑，存在反转机会。用收益符号与量能z-score的乘积衡量背离强度，负值预示反转。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div_003",
            name="Volume-Price Divergence",
            display_name="量价背离因子",
            description="短期收益方向与成交量变化的背离程度：当价格上涨但成交量萎缩（或下跌但放量）时，趋势可持续性存疑，存在反转机会。用收益符号与量能z-score的乘积衡量背离强度，负值预示反转。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_z = (data['volume'] - data['volume'].rolling(20).mean()) / (data['volume'].rolling(20).std() + 1e-9)
        result = (-ret * vol_z).rolling(3).mean().clip(-1, 1)
        return result
