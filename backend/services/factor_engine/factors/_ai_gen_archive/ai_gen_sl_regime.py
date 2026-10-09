"""AI因子: 止损区间过滤 | 置信:55% | 针对止损亏损模式，在未知市场状态下过滤高波动假突破。当价格快速偏离均线且成交量异常时，降低反向交易信号强度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Stoplossregimefilter(BaseFactor):
    """针对止损亏损模式，在未知市场状态下过滤高波动假突破。当价格快速偏离均线且成交量异常时，降低反向交易信号强度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_sl_regime",
            name="StopLossRegimeFilter",
            display_name="止损区间过滤",
            description="针对止损亏损模式，在未知市场状态下过滤高波动假突破。当价格快速偏离均线且成交量异常时，降低反向交易信号强度。",
            category="composite",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ret = data['close'].pct_change(5)
        vol = data['close'].pct_change().rolling(10).std()
        vol_ma = data['close'].pct_change().rolling(20).std()
        vol_spike = vol / (vol_ma + 1e-9)
        vol_ratio = (data['volume'].rolling(5).mean() / (data['volume'].rolling(20).mean() + 1e-9))
        result = (ret * (1 - vol_spike) * (1 - vol_ratio)).clip(-1, 1)
        return result
