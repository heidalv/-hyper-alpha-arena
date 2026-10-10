"""AI因子: 量价背离 | 置信:55% | 价格上行但成交量萎缩（缩量上涨）暗示动能不足，价格下行但放量暗示恐慌抛售后的反弹机会。用价格变化方向与成交量相对中位数偏离的乘积构造量价背离因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格上行但成交量萎缩（缩量上涨）暗示动能不足，价格下行但放量暗示恐慌抛售后的反弹机会。用价格变化方向与成交量相对中位数偏离的乘积构造量价背离因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离",
            description="价格上行但成交量萎缩（缩量上涨）暗示动能不足，价格下行但放量暗示恐慌抛售后的反弹机会。用价格变化方向与成交量相对中位数偏离的乘积构造量价背离因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_ratio = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        result = (ret * (1 - vol_ratio.clip(0, 3) / 3)).clip(-1, 1)
        return result
