"""AI因子: 微小反转风险 | 置信:50% | 识别短期微小波动后可能发生的反向急跌/急涨风险，基于价格变化与ATR的比值以及连续同向小K线数量。当短期趋势极弱且连续小K线后出现反向信号时，因子值接近-1（高风险）；趋势强劲时接近+1。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Micro_Reversal_Risk(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_micro_reversal", name="Micro_Reversal_Risk",
        display_name="微小反转风险", description="识别短期微小波动后可能发生的反向急跌/急涨风险，基于价格变化与ATR的比值以及连续同向小K线数量。当短期趋势极弱且连续小K线后出现反向信号时，因子值接近-1（高风险）；趋势强劲时接近+1。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data: pd.DataFrame) -> pd.Series:
    import pandas as pd
    import numpy as np
    
    high = data['high']
    low = data['low']
    close = data['close']
    open_ = data['open']
    volume = data['volume']
    
    # 计算ATR
    tr = pd.concat([high - low,
                    (high - close.shift()).abs(),
                    (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    
    # 价格变化率（1周期）
    ret = close.pct_change()
    
    # 连续同向小K线计数：K线实体小于0.3*ATR且方向相同
    body = (close - open_).abs()
    small_body = body < (0.3 * atr)
    direction = np.sign(close - open_)
    
    # 用rolling计数连续满足small_body且direction相同
    conseq = 0
    conseq_list = []
    for i in range(len(data)):
        if small_body.iloc[i] and direction.iloc[i] != 0:
            if i > 0 and small_body.iloc[i-1] and direction.iloc[i] == direction.iloc[i-1]:
                conseq += 1
            else:
                conseq = 1
        else:
            conseq = 0
        conseq_list.append(conseq)
    conseq_series = pd.Series(conseq_list, index=data.index)
    
    # 当前价格相对过去N周期位置
    n = 10
    rolling_max = close.rolling(n).max()
    rolling_min = close.rolling(n).min()
    position = (close - rolling_min) / (rolling_max - rolling_min + 1e-10)
    
    # 因子核心：当连续小K线>=3且价格处于极端位置时，反转风险高
    risk_signal = (conseq_series >= 3).astype(float) * (1 - 2 * np.abs(position - 0.5))
    # 加上动量衰减：最近3日收益若为负且当前价格高于前高，信号加强
    momentum = ret.rolling(3).sum()
    # 组合
    factor = -risk_signal * np.sign(momentum) * 0.8 + 0.2 * (momentum / (momentum.abs() + 1e-10))
    factor = factor.clip(-1, 1)
    return factor.fillna(0).round(6)
