"""AI因子: 均线环境 | 置信:70% | 衡量当前价格相对于20日均线的偏离程度，并用ATR归一化。当价格靠近均线时，市场方向不明确，做空易被微小波动止损，因子输出正值。当价格远离均线时，趋势清晰，输出负值。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Moving Average Envelope(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_maenv", name="Moving Average Envelope",
        display_name="均线环境", description="衡量当前价格相对于20日均线的偏离程度，并用ATR归一化。当价格靠近均线时，市场方向不明确，做空易被微小波动止损，因子输出正值。当价格远离均线时，趋势清晰，输出负值。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high, low = data['high'], data['low']
    ma = close.rolling(20).mean()
    true_range = pd.concat([high - low, abs(high - close.shift()), abs(low - close.shift())], axis=1).max(axis=1)
    atr = true_range.rolling(14).mean()
    deviation = abs(close - ma) / (atr * 2 + 1e-10)
    # 当deviation<1时，价格在均线附近，因子>0；否则因子负向
    factor = 1 - 2 * deviation.clip(0, 1)
    return factor.fillna(0).clip(-1, 1)
