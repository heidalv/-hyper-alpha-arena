"""AI因子: 持仓超时因子 | 置信:60% | 捕捉长期横盘后波动率突然激增的风险。计算近期价格平均真实波幅(ATR)的滚动变化率，并结合价格偏离移动平均的程度。若波动率突然放大且价格偏离较大则警告。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Hold Timeout Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_hold_timeout", name="Hold Timeout Factor",
        display_name="持仓超时因子", description="捕捉长期横盘后波动率突然激增的风险。计算近期价格平均真实波幅(ATR)的滚动变化率，并结合价格偏离移动平均的程度。若波动率突然放大且价格偏离较大则警告。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    high = data['high']
    low = data['low']
    close = data['close']
    # ATR (14周期)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    # ATR变化率(1期)
    atr_pct = atr.pct_change()
    # 价格偏离30日均线百分比
    ma30 = close.rolling(30).mean()
    dev = (close - ma30) / ma30
    # 组合：ATR突然增大(>2倍标准差)且价格偏离绝对值大 => 负向
    atr_z = (atr_pct - atr_pct.rolling(60).mean()) / atr_pct.rolling(60).std()
    factor = -atr_z * dev.abs()
    factor = factor.replace([np.inf, -np.inf], np.nan).fillna(0)
    return np.tanh(factor / 2.0)
