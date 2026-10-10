"""AI因子: 量价背离 | 置信:58% | 价格短期涨幅与成交量相对均值的偏离交互：放量上涨（量价共振）看涨，缩量上涨（量价背离）看跌。用成交量z-score与收益方向乘积衡量确认强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格短期涨幅与成交量相对均值的偏离交互：放量上涨（量价共振）看涨，缩量上涨（量价背离）看跌。用成交量z-score与收益方向乘积衡量确认强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_div",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格短期涨幅与成交量相对均值的偏离交互：放量上涨（量价共振）看涨，缩量上涨（量价背离）看跌。用成交量z-score与收益方向乘积衡量确认强度。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vmean = data['volume'].rolling(20).mean()
        vstd = data['volume'].rolling(20).std() + 1e-9
        vz = (data['volume'] - vmean) / vstd
        result = (ret * vz).rolling(3).mean().clip(-1, 1)
        return result
