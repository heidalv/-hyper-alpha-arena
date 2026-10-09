"""AI因子: 未知状态防御因子 | 置信:55% | 针对regime=unknown时系统性亏损，用价格在布林带内的位置与成交量变化衡量市场状态的不确定性。当价格处于带内中间且成交量萎缩时，状态不明，因子向-1；当价格突破带边缘且成交量放大时，状态明确，因子向+1。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class RegimeUncertaintyDefense(BaseFactor):
    """针对regime=unknown时系统性亏损，用价格在布林带内的位置与成交量变化衡量市场状态的不确定性。当价格处于带内中间且成交量萎缩时，状态不明，因子向-1；当价格突破带边缘且成交量放大时，状态明确，因子向+1。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_regime_unknown_defense",
            name="Regime_Uncertainty_Defense",
            display_name="未知状态防御因子",
            description="针对regime=unknown时系统性亏损，用价格在布林带内的位置与成交量变化衡量市场状态的不确定性。当价格处于带内中间且成交量萎缩时，状态不明，因子向-1；当价格突破带边缘且成交量放大时，状态明确，因子向+1。",
            category="behavioral",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        mid = data['close'].rolling(20).mean()
        std = data['close'].rolling(20).std()
        upper = mid + 2 * std
        lower = mid - 2 * std
        pos = (data['close'] - lower) / (upper - lower + 1e-9)
        vol_ratio = data['volume'] / (data['volume'].rolling(20).mean() + 1e-9)
        result = ((pos - 0.5) * 2 * 0.6 + (vol_ratio - 1) * 0.4).clip(-1, 1)
        return result
