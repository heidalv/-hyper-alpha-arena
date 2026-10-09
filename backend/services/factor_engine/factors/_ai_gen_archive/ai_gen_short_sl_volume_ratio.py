"""AI因子: 空头止损量价背离 | 置信:58% | 针对空头止损亏损模式（UNI/VIRTUAL），捕捉价格短暂反弹但成交量未能配合的弱势状态。当价格高于短期均线但成交量低于长期中位数时，表明反弹缺乏资金支持，适合做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class ShortSLVolumeDivergence(BaseFactor):
    """针对空头止损亏损模式（UNI/VIRTUAL），捕捉价格短暂反弹但成交量未能配合的弱势状态。当价格高于短期均线但成交量低于长期中位数时，表明反弹缺乏资金支持，适合做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_short_sl_volume_ratio",
            name="Short_SL_Volume_Divergence",
            display_name="空头止损量价背离",
            description="针对空头止损亏损模式（UNI/VIRTUAL），捕捉价格短暂反弹但成交量未能配合的弱势状态。当价格高于短期均线但成交量低于长期中位数时，表明反弹缺乏资金支持，适合做空。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        short_ma = data['close'].rolling(5).mean()
        long_ma = data['close'].rolling(20).mean()
        price_above = (data['close'] > short_ma).astype(float)
        vol_ratio = data['volume'] / (data['volume'].rolling(50).median() + 1e-9)
        vol_low = (vol_ratio < 0.8).astype(float)
        trend_weak = (short_ma < long_ma).astype(float)
        result = -1 * price_above * vol_low * trend_weak
        result = result.clip(-1, 1)
        return result
