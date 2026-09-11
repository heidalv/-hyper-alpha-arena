"""AI因子: 量价背离 | 置信:50% | 价格短期涨幅与成交量变化方向背离时提示反转：价升量缩为负信号，价跌量增为承接正信号，用收益与量变的符号差异构造背离因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化方向背离时提示反转：价升量缩为负信号，价跌量增为承接正信号，用收益与量变的符号差异构造背离因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期涨幅与成交量变化方向背离时提示反转：价升量缩为负信号，价跌量增为承接正信号，用收益与量变的符号差异构造背离因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        volchg = data['volume'].pct_change(5)
        result = (np.sign(ret) * np.sign(volchg) * ret.abs()).clip(-1, 1)
        return result
