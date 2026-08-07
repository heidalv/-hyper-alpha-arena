"""AI因子: 波动率流动性陷阱检测 | 置信:65% | 通过计算ATR与成交量的比值变化，识别高波动但成交量萎缩的陷阱区域。当ATR扩大的同时成交量下降，表明市场流动性不足，容易导致虚假突破和滑点亏损。因子值接近+1表示危险，建议避免开仓；接近-1表示安全。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility-Liquidity Trap Detector(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_trap", name="Volatility-Liquidity Trap Detector",
        display_name="波动率流动性陷阱检测", description="通过计算ATR与成交量的比值变化，识别高波动但成交量萎缩的陷阱区域。当ATR扩大的同时成交量下降，表明市场流动性不足，容易导致虚假突破和滑点亏损。因子值接近+1表示危险，建议避免开仓；接近-1表示安全。",
        category="composite", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    # data: DataFrame with columns ['open','high','low','close','volume']
    import numpy as np
    # 计算ATR
    high = data['high']
    low = data['low']
    close = data['close']
    prev_close = close.shift(1)
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_close), np.abs(low - prev_close)))
    atr = tr.rolling(14).mean()
    
    # 计算成交量变化
    vol = data['volume']
    vol_ma = vol.rolling(14).mean()
    vol_ratio = vol / vol_ma  # 当前成交量相对均值比值
    
    # 计算ATR变化率
    atr_prev = atr.shift(1)
    atr_change = (atr - atr_prev) / atr_prev
    
    # 当ATR上升且成交量下降时，信号接近+1
    # 使用tanh将信号映射到[-1,1]
    raw = np.where((atr_change > 0.01) & (vol_ratio < 0.9), 1, 0)
    raw = raw * 2 - 1  # 转为-1到1
    # 平滑处理
    smoothed = raw.rolling(3).mean().fillna(0)
    return np.clip(smoothed, -1, 1)
