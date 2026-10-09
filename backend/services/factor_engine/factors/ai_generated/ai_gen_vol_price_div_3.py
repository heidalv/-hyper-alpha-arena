"""AI因子: 量价背离反转 | 置信:55% | 价格短期涨幅与成交量变化方向背离时，说明上涨缺乏量能支撑，容易反转。用价格动量与量能变化的差值衡量背离程度，缩量上涨给负信号，放量下跌给正信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化方向背离时，说明上涨缺乏量能支撑，容易反转。用价格动量与量能变化的差值衡量背离程度，缩量上涨给负信号，放量下跌给正信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div_3",
            name="Volume Price Divergence",
            display_name="量价背离反转",
            description="价格短期涨幅与成交量变化方向背离时，说明上涨缺乏量能支撑，容易反转。用价格动量与量能变化的差值衡量背离程度，缩量上涨给负信号，放量下跌给正信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = ((ret - vol_chg / vol_std).clip(-1, 1))
        return result
