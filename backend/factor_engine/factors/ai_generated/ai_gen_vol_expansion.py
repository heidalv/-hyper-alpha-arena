"""AI因子: 波动率扩张风险 | 置信:60% | 衡量价格波动率从低水平突然扩张的程度，通常预示着行情剧烈震荡后的回调或反转风险。高扩张时值为负，低扩张时为正。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Expansion Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_expansion", name="Volatility Expansion Risk",
        display_name="波动率扩张风险", description="衡量价格波动率从低水平突然扩张的程度，通常预示着行情剧烈震荡后的回调或反转风险。高扩张时值为负，低扩张时为正。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # 计算ATR
    high = data['high']
    low = data['low']
    close = data['close']
    tr = pd.DataFrame({
        'hl': high - low,
        'hc': (high - close.shift(1)).abs(),
        'lc': (low - close.shift(1)).abs()
    }).max(axis=1)
    atr = tr.rolling(window=14).mean()
    # 计算ATR的短期变化率
    atr_chg = (atr - atr.shift(5)) / atr.shift(5)
    # 结合历史波动率百分位
    vol_percentile = atr.rolling(window=50).apply(lambda x: (x[-1] - x.min()) / (x.max() - x.min() + 1e-10), raw=True)
    # 当波动率突然扩张且处于高位时风险大
    expansion = atr_chg * vol_percentile
    # 归一化到[-1,1]
    result = -expansion.fillna(0).clip(-1, 1)
    return result
