"""AI因子: 价量背离因子 | 置信:60% | 通过比较价格区间与成交量变化，识别趋势衰竭或反转信号。当价格创新高而成交量萎缩时视为看跌，反之为看涨。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class PriceVolumeDivergence(BaseFactor):
    """通过比较价格区间与成交量变化，识别趋势衰竭或反转信号。当价格创新高而成交量萎缩时视为看跌，反之为看涨。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_pricerange",
            name="Price-Volume Divergence",
            display_name="价量背离因子",
            description="通过比较价格区间与成交量变化，识别趋势衰竭或反转信号。当价格创新高而成交量萎缩时视为看跌，反之为看涨。",
            category="composite",
            subcategory="contrarian",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        n = 14
        # 价格区间相对宽度
        high_low = data['high'] - data['low']
        avg_range = high_low.rolling(n).mean()
        range_ratio = high_low / avg_range
        # 成交量相对变化
        vol_chg = data['volume'].pct_change(n)
        # 背离：价格区间扩大但成交量萎缩 => 负信号
        raw = -range_ratio * vol_chg
        # 平滑
        raw = raw.rolling(3).mean()
        max_abs = raw.abs().max()
        if max_abs == 0 or np.isnan(max_abs):
            return pd.Series(0.0, index=data.index)
        result = raw / max_abs
        return result.fillna(0.0).clip(-1, 1)
