"""AI因子: 波动率突破背离 | 置信:65% | 识别价格在窄幅盘整后突然大幅波动但收盘未能有效突破支撑或阻力，预示假突破，容易引发反向操作亏损。计算近期ATR与价格变动幅度之比的异常值，并取负值表示假突破风险高。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Breakout Divergence(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vbd", name="Volatility Breakout Divergence",
        display_name="波动率突破背离", description="识别价格在窄幅盘整后突然大幅波动但收盘未能有效突破支撑或阻力，预示假突破，容易引发反向操作亏损。计算近期ATR与价格变动幅度之比的异常值，并取负值表示假突破风险高。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # 计算ATR(14)
    high = data['high']
    low = data['low']
    close = data['close']
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    # 价格短期变动幅度（3周期）
    price_range = (high.rolling(3).max() - low.rolling(3).min())
    # 波动率扩张因子：ATR 突增相对于价格范围
    atr_ratio = atr / price_range.replace(0, np.nan)
    atr_ratio_z = (atr_ratio - atr_ratio.rolling(20).mean()) / atr_ratio.rolling(20).std()
    # 收盘位置离区间边缘的距离
    mid = (high.rolling(3).max() + low.rolling(3).min()) / 2
    dist = (close - mid).abs() / (price_range + 1e-10)
    # 假突破信号: 波动率大幅扩张但收盘在区间中间附近
    signal = -np.clip(atr_ratio_z * (1 - dist * 2), -1, 1)
    return signal.fillna(0)
