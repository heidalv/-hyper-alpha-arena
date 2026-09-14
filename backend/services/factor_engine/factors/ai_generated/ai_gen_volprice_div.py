"""AI因子: 量价背离因子 | 置信:58% | 比较价格短期变化方向与成交量短期变化方向的一致性。当价格上涨但成交量萎缩（背离）时，趋势脆弱，未来回落概率高；价格下跌但缩量时，抛压衰减，反弹概率高。用价格收益与量能变化的符号乘积构造。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """比较价格短期变化方向与成交量短期变化方向的一致性。当价格上涨但成交量萎缩（背离）时，趋势脆弱，未来回落概率高；价格下跌但缩量时，抛压衰减，反弹概率高。用价格收益与量能变化的符号乘积构造。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volprice_div",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="比较价格短期变化方向与成交量短期变化方向的一致性。当价格上涨但成交量萎缩（背离）时，趋势脆弱，未来回落概率高；价格下跌但缩量时，抛压衰减，反弹概率高。用价格收益与量能变化的符号乘积构造。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        result = (-(ret * (vol_chg / vol_std))).clip(-1, 1)
        return result
