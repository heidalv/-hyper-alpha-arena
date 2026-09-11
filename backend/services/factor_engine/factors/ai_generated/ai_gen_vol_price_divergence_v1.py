"""AI因子: 量价背离因子 | 置信:55% | 价格上涨但成交量萎缩(或价跌量增)时，量价背离预示趋势不可持续。用短期收益方向与成交量变化方向的一致性构造背离信号，捕捉反转alpha。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上涨但成交量萎缩(或价跌量增)时，量价背离预示趋势不可持续。用短期收益方向与成交量变化方向的一致性构造背离信号，捕捉反转alpha。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence_v1",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="价格上涨但成交量萎缩(或价跌量增)时，量价背离预示趋势不可持续。用短期收益方向与成交量变化方向的一致性构造背离信号，捕捉反转alpha。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std() + 1e-9
        norm_vol = vol_chg / vol_std
        result = (-ret * norm_vol).clip(-1, 1)
        return result
