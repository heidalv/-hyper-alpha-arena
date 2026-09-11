"""AI因子: 量价背离 | 置信:55% | 价格短期涨幅与成交量相对强度背离:价涨量缩视为动能不足(看空),价跌量缩视为抛压衰竭(看多),用成交量z-score与收益方向交互构造反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量相对强度背离:价涨量缩视为动能不足(看空),价跌量缩视为抛压衰竭(看多),用成交量z-score与收益方向交互构造反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期涨幅与成交量相对强度背离:价涨量缩视为动能不足(看空),价跌量缩视为抛压衰竭(看多),用成交量z-score与收益方向交互构造反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vmean = data['volume'].rolling(20).mean()
        vstd = data['volume'].rolling(20).std()
        vz = (data['volume'] - vmean) / (vstd + 1e-9)
        result = (-ret * vz).clip(-1, 1)
        return result
