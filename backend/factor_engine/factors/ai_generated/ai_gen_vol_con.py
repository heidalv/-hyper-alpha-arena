"""AI因子: 波动率收缩突破因子 | 置信:60% | 检测波动率收缩后的突破行为。使用ATR（14日）的相对变化，当当前K线振幅与ATR的比值小于近期0.25分位数且成交量异常放大时，随后价格偏离近期区间则视为反转信号。最终输出价格相对于近期区间的分位数方向信号。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Contraction Breakout(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_con", name="Volatility Contraction Breakout",
        display_name="波动率收缩突破因子", description="检测波动率收缩后的突破行为。使用ATR（14日）的相对变化，当当前K线振幅与ATR的比值小于近期0.25分位数且成交量异常放大时，随后价格偏离近期区间则视为反转信号。最终输出价格相对于近期区间的分位数方向信号。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    tr = np.maximum(data['high'] - data['low'], np.abs(data['high'] - data['close'].shift(1)), np.abs(data['low'] - data['close'].shift(1)))
    atr = tr.rolling(14).mean()
    range_ratio = tr / (atr + 1e-10)
    vol_shrink = range_ratio < range_ratio.rolling(50).quantile(0.25)
    vol_ratio = data['volume'] / data['volume'].rolling(20).mean()
    # 价格突破近期区间（10日高低）
    recent_high = data['high'].rolling(10).max().shift(1)
    recent_low = data['low'].rolling(10).min().shift(1)
    breakout_up = data['high'] > recent_high
    breakout_down = data['low'] < recent_low
    # 信号：波动收缩且成交量放大且突破，则反向
    signal = np.where(vol_shrink & (vol_ratio > 1.5) & breakout_down, 1.0,
                      np.where(vol_shrink & (vol_ratio > 1.5) & breakout_up, -1.0, 0.0))
    return pd.Series(signal, index=data.index)
