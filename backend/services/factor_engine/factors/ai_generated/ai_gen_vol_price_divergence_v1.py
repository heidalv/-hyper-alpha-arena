"""AI因子: 量价背离反转 | 置信:58% | 价格短期涨幅与成交量变化方向背离时，往往预示趋势不可持续。当价格上涨但成交量萎缩(量价背离)时看空，价格下跌但放量时看多。用成交量z-score与收益符号的乘积构造反转信号。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量变化方向背离时，往往预示趋势不可持续。当价格上涨但成交量萎缩(量价背离)时看空，价格下跌但放量时看多。用成交量z-score与收益符号的乘积构造反转信号。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence_v1",
            name="Volume Price Divergence",
            display_name="量价背离反转",
            description="价格短期涨幅与成交量变化方向背离时，往往预示趋势不可持续。当价格上涨但成交量萎缩(量价背离)时看空，价格下跌但放量时看多。用成交量z-score与收益符号的乘积构造反转信号。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ma = data['volume'].rolling(20).mean() + 1e-9
        vol_z = (data['volume'] - vol_ma) / (data['volume'].rolling(20).std() + 1e-9)
        result = (-ret * vol_z).rolling(3).mean().clip(-1, 1)
        return result
