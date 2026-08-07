"""AI因子: 归一化范围波动 | 置信:60% | 通过最近N周期的高低价范围与ATR的比值，衡量当前价格在区间内的位置和波动程度，当价格接近区间边界且波动放大时，市场可能处于未知状态，倾向于反向做空。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Normalized_Range_Volatility(BaseFactor):
    """通过最近N周期的高低价范围与ATR的比值，衡量当前价格在区间内的位置和波动程度，当价格接近区间边界且波动放大时，市场可能处于未知状态，倾向于反向做空。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_nrv",
            name="Normalized Range Volatility",
            display_name="归一化范围波动",
            description="通过最近N周期的高低价范围与ATR的比值，衡量当前价格在区间内的位置和波动程度，当价格接近区间边界且波动放大时，市场可能处于未知状态，倾向于反向做空。",
            category="volatility",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        high = data['high']
        low = data['low']
        close = data['close']
        tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
        atr = tr.rolling(20).mean()
        range_ratio = (close - low.rolling(20).min()) / (high.rolling(20).max() - low.rolling(20).min() + 1e-10)
        nrv = (range_ratio - 0.5) * 2 * (atr / close.mean())
        result = np.clip(nrv, -1, 1)
        return result
