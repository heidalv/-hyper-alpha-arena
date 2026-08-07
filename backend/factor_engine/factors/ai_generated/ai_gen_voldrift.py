"""AI因子: 波动偏离因子 | 置信:65% | 衡量当前价格波动相对于近期平均波动率的偏离程度，结合方向性判断。当波动率显著低于历史均值且价格处于区间中部时，预测价格将突破或延续震荡，给出负向信号（避免追涨杀跌）。计算近期N日真实波幅均值ATR，并除以过去M日ATR标准差，再乘以价格方向符号（当前收盘价相对于过去N日均值的方向）。最后用tanh压缩到[-1,1]。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Drift Divergence(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_voldrift", name="Volatility Drift Divergence",
        display_name="波动偏离因子", description="衡量当前价格波动相对于近期平均波动率的偏离程度，结合方向性判断。当波动率显著低于历史均值且价格处于区间中部时，预测价格将突破或延续震荡，给出负向信号（避免追涨杀跌）。计算近期N日真实波幅均值ATR，并除以过去M日ATR标准差，再乘以价格方向符号（当前收盘价相对于过去N日均值的方向）。最后用tanh压缩到[-1,1]。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    # data: DataFrame with columns ['open','high','low','close','volume']
    N = 20  # 短期
    M = 60  # 长期
    # ATR
    high_low = data['high'] - data['low']
    high_close = np.abs(data['high'] - data['close'].shift(1))
    low_close = np.abs(data['low'] - data['close'].shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr_short = tr.rolling(N).mean()
    atr_long = tr.rolling(M).mean()
    # 波动率偏离：短期ATR相对于长期均值的z-score
    atr_std = tr.rolling(M).std(ddof=0)
    vol_z = (atr_short - atr_long) / atr_std.replace(0, np.nan)
    # 价格方向：close相对于过去N日均值
    close_ma = data['close'].rolling(N).mean()
    direction = (data['close'] - close_ma) / close_ma
    # 结合：偏离为正且方向一致时加强，否则减弱
    raw = vol_z * direction
    # 归一化，使用tanh限制范围并保持符号
    result = np.tanh(raw)
    # 处理NaN，用0填充
    result = result.fillna(0.0)
    return result
