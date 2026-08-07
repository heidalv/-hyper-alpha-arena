"""AI因子: 利润回撤风险 | 置信:60% | 计算当前收盘价相对过去20天最高点的回撤百分比与平均真实波幅(ATR20)的比值，若比值大于1.5，表明回撤幅度远超正常波动，可能触发利润回撤止损，输出负向信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Profit Drawdown Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_pdr", name="Profit Drawdown Risk",
        display_name="利润回撤风险", description="计算当前收盘价相对过去20天最高点的回撤百分比与平均真实波幅(ATR20)的比值，若比值大于1.5，表明回撤幅度远超正常波动，可能触发利润回撤止损，输出负向信号。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data must have high, low, close columns
    high = data['high']
    low = data['low']
    close = data['close']
    # ATR
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr20 = tr.rolling(20).mean()
    highest20 = high.rolling(20).max()
    drawdown = (highest20 - close) / atr20
    condition = drawdown > 1.5
    result = pd.Series(1.0, index=data.index)
    result[condition] = -1.0
    return result
