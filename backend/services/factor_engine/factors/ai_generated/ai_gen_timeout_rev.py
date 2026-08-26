"""AI因子: 超时均值回归强度 | 置信:58% | 针对持仓超时导致的亏损，识别价格在持仓周期内未有效突破而回归均值的倾向。当价格偏离均线过大且波动率下降时，反向信号增强。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Timeoutmeanreversion(BaseFactor):
    """针对持仓超时导致的亏损，识别价格在持仓周期内未有效突破而回归均值的倾向。当价格偏离均线过大且波动率下降时，反向信号增强。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_timeout_rev",
            name="TimeoutMeanReversion",
            display_name="超时均值回归强度",
            description="针对持仓超时导致的亏损，识别价格在持仓周期内未有效突破而回归均值的倾向。当价格偏离均线过大且波动率下降时，反向信号增强。",
            category="technical",
            subcategory="mean_reversion",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        ma = data['close'].rolling(15).mean()
        dev = (data['close'] - ma) / (ma + 1e-9)
        vol = data['close'].pct_change().rolling(15).std()
        vol_norm = vol / (data['close'].pct_change().rolling(30).std() + 1e-9)
        result = (-dev * (1 - vol_norm)).clip(-1, 1)
        return result
