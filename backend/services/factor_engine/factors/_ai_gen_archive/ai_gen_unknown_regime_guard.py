"""AI因子: 未知状态防御因子 | 置信:50% | 针对regime=unknown下的系统性亏损，通过价格通道位置与成交量异常度组合，识别市场状态不明朗时的危险区间。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class UnknownRegimeGuard(BaseFactor):
    """针对regime=unknown下的系统性亏损，通过价格通道位置与成交量异常度组合，识别市场状态不明朗时的危险区间。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_unknown_regime_guard",
            name="Unknown Regime Guard",
            display_name="未知状态防御因子",
            description="针对regime=unknown下的系统性亏损，通过价格通道位置与成交量异常度组合，识别市场状态不明朗时的危险区间。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high'].rolling(20).max()
        low = data['low'].rolling(20).min()
        pos = (data['close'] - low) / (high - low + 1e-9)
        vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
        result = ((pos - 0.5) * 2 * (vol_ratio - 1).clip(-1, 1)).clip(-1, 1)
        return result
