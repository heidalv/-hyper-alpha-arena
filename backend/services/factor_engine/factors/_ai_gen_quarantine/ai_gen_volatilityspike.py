"""AI因子: 波动率异常反转因子 | 置信:60% | 检测价格相对于短期均线的偏离度是否过大，同时结合ATR波动率异常放大。当价格偏离均线超过2倍ATR且波动率在放大时，认为存在假突破或回调风险，做空；反之价格低于均线且波动率放大时做多。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Volatility_Spike_Reversal_Indicator(BaseFactor):
    """检测价格相对于短期均线的偏离度是否过大，同时结合ATR波动率异常放大。当价格偏离均线超过2倍ATR且波动率在放大时，认为存在假突破或回调风险，做空；反之价格低于均线且波动率放大时做多。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_volatilityspike",
            name="Volatility Spike Reversal Indicator",
            display_name="波动率异常反转因子",
            description="检测价格相对于短期均线的偏离度是否过大，同时结合ATR波动率异常放大。当价格偏离均线超过2倍ATR且波动率在放大时，认为存在假突破或回调风险，做空；反之价格低于均线且波动率放大时做多。",
            category="technical",
            subcategory="volatility",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        df = data.copy()
        close = df['close']
        high = df['high']
        low = df['low']
        # Parameters
        ma_period = 20
        atr_period = 14
        multiplier = 2.0
        # MA and ATR
        ma = close.rolling(ma_period).mean()
        tr = np.maximum(high - low, np.abs(high - close.shift()), np.abs(low - close.shift()))
        atr = tr.rolling(atr_period).mean()
        # Deviation
        dev = (close - ma) / (atr + 1e-10)
        # Rolling std of deviation to detect spike
        dev_std = dev.rolling(20).std()
        spike = np.abs(dev) > multiplier * (dev_std + 1e-10)
        # Factor: if spike and dev>0 => -1 (short), if spike and dev<0 => +1 (long), else 0
        factor = pd.Series(np.where(spike & (dev > 0), -1.0, np.where(spike & (dev < 0), 1.0, 0.0)), index=df.index)
        factor = factor.fillna(0)
        return factor
