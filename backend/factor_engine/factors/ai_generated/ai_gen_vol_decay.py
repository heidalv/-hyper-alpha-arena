"""AI因子: 波动率衰减 | 置信:60% | 衡量近期波动率相对于历史均值的衰减程度。当ATR比值低于0.8时认为波动率低，容易导致微利平仓或假突破亏损，给出负向信号；高于1.2时波动率高，给出正向信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Decay(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_decay", name="Volatility Decay",
        display_name="波动率衰减", description="衡量近期波动率相对于历史均值的衰减程度。当ATR比值低于0.8时认为波动率低，容易导致微利平仓或假突破亏损，给出负向信号；高于1.2时波动率高，给出正向信号。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: DataFrame with columns ['open','high','low','close','volume']
    hl = data['high'] - data['low']
    hc = (data['high'] - data['close'].shift()).abs()
    lc = (data['low'] - data['close'].shift()).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    atr5 = tr.rolling(5).mean()
    atr20 = tr.rolling(20).mean()
    ratio = atr5 / atr20
    ratio = ratio.fillna(1.0)
    # 线性映射到[-1,1]: 0.8->-1, 1.2->1
    result = ((ratio - 0.8) / (1.2 - 0.8)) * 2 - 1
    result = result.clip(-1, 1)
    return result
