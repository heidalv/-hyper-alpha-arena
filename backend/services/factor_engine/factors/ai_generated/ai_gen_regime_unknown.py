"""AI因子: 未知状态风险规避 | 置信:40% | 针对regime=unknown时频繁亏损的现象，该因子通过检测价格在近期区间内的位置和成交量异常来识别不确定市场状态。当价格处于区间中部且成交量萎缩时，市场方向不明，容易导致止损或超时亏损。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUnknownRisk(BaseFactor):
    """针对regime=unknown时频繁亏损的现象，该因子通过检测价格在近期区间内的位置和成交量异常来识别不确定市场状态。当价格处于区间中部且成交量萎缩时，市场方向不明，容易导致止损或超时亏损。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown",
            name="regime_unknown_risk",
            display_name="未知状态风险规避",
            description="针对regime=unknown时频繁亏损的现象，该因子通过检测价格在近期区间内的位置和成交量异常来识别不确定市场状态。当价格处于区间中部且成交量萎缩时，市场方向不明，容易导致止损或超时亏损。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high_20 = data['high'].rolling(20).max()
        low_20 = data['low'].rolling(20).min()
        mid = (high_20 + low_20) / 2
        pos = (data['close'] - low_20) / (high_20 - low_20 + 1e-9)
        vol_ma = data['volume'].rolling(20).mean()
        vol_ratio = data['volume'] / (vol_ma + 1e-9)
        result = ((pos - 0.5).abs() * -1 + (vol_ratio - 1) * -0.5).clip(-1, 1)
        return result
