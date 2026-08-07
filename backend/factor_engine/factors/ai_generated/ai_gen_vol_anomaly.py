"""AI因子: 波动率状态异常 | 置信:60% | 通过对比短期（5周期）和长期（20周期）ATR的比值，捕捉波动率突变。当比值超过阈值（如1.5）时，表明波动率结构突变，市场进入未知状态，因子输出负值（-1）警告；正常状态输出正值（+1）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Regime Anomaly(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_anomaly", name="Volatility Regime Anomaly",
        display_name="波动率状态异常", description="通过对比短期（5周期）和长期（20周期）ATR的比值，捕捉波动率突变。当比值超过阈值（如1.5）时，表明波动率结构突变，市场进入未知状态，因子输出负值（-1）警告；正常状态输出正值（+1）。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    high, low, close = data['high'], data['low'], data['close']
    tr = np.maximum(high - low, np.maximum(abs(high - close.shift(1)), abs(low - close.shift(1))))
    atr_short = tr.rolling(5).mean()
    atr_long = tr.rolling(20).mean()
    ratio = atr_short / atr_long
    # 当ratio大于1.5或小于0.5时视为异常，返回-1，否则+1
    result = pd.Series(np.where((ratio > 1.5) | (ratio < 0.5), -1, 1), index=data.index)
    return result
