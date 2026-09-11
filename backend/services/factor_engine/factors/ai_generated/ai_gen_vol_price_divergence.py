"""AI因子: 量价背离因子 | 置信:58% | 价格变化与成交量变化的背离程度。放量上涨确认趋势，缩量上涨预示衰竭。用价格收益与量能变化的符号一致性构建因子。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class VolumePriceDivergence(BaseFactor):
    """价格变化与成交量变化的背离程度。放量上涨确认趋势，缩量上涨预示衰竭。用价格收益与量能变化的符号一致性构建因子。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_vol_price_divergence",
            name="Volume Price Divergence",
            display_name="量价背离因子",
            description="价格变化与成交量变化的背离程度。放量上涨确认趋势，缩量上涨预示衰竭。用价格收益与量能变化的符号一致性构建因子。",
            category="technical",
            subcategory="volume",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol_chg = data['volume'].pct_change(5)
        vol_std = data['volume'].pct_change().rolling(20).std()
        result = (ret * (vol_chg / (vol_std + 1e-9))).clip(-1, 1)
        return result
