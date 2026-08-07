"""AI因子: 趋势波动率比 | 置信:60% | 计算短期趋势强度与波动率的比值，用于判断当前趋势的可靠性。当趋势强且波动低时值为正（看涨），当趋势弱或波动高时值为负（看跌）。使用收盘价的短期均线斜率代表趋势，ATR代表波动率。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend_Volatility_Ratio(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_trenvol", name="Trend_Volatility_Ratio",
        display_name="趋势波动率比", description="计算短期趋势强度与波动率的比值，用于判断当前趋势的可靠性。当趋势强且波动低时值为正（看涨），当趋势弱或波动高时值为负（看跌）。使用收盘价的短期均线斜率代表趋势，ATR代表波动率。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    high = data['high']
    low = data['low']
    # 短期均线斜率（5周期）
    ma5 = close.rolling(5).mean()
    slope = ma5.diff(3)  # 3周期变化
    # ATR
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    # 避免除零
    ratio = slope / (atr + 1e-10)
    # 归一化到[-1,1]，使用tanh压缩
    result = np.tanh(ratio * 0.1)
    return result.fillna(0.0)
