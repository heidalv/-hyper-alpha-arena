"""AI因子: 波动率压缩反转 | 置信:65% | 检测价格波动率压缩到极低水平后的突破方向，并预测均值回归。计算过去20天最高价与最低价之差的标准差，当最近5天波动率低于20天波动率的20%分位数时判定为压缩。然后计算收盘价与20日均线的偏离度，若偏离超过1倍近期ATR则预测回归。正值表示看涨（价格从下方突破回归），负值看跌。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility_Squeeze_Reversal(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vsq", name="Volatility_Squeeze_Reversal",
        display_name="波动率压缩反转", description="检测价格波动率压缩到极低水平后的突破方向，并预测均值回归。计算过去20天最高价与最低价之差的标准差，当最近5天波动率低于20天波动率的20%分位数时判定为压缩。然后计算收盘价与20日均线的偏离度，若偏离超过1倍近期ATR则预测回归。正值表示看涨（价格从下方突破回归），负值看跌。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    # 波动率: 每日高低差的滚动标准差
    hl = data['high'] - data['low']
    vol_std = hl.rolling(20).std()
    recent_vol = hl.rolling(5).mean()
    # 低波动条件: 最近5日均值低于过去20天波动率标准的20%分位数
    vol_quantile = vol_std.rolling(20).quantile(0.2)
    squeeze = (recent_vol < vol_quantile).astype(int)
    # 价格偏离: 收盘价与20日均线
    ma20 = data['close'].rolling(20).mean()
    atr = (data['high'] - data['low']).rolling(14).mean()  # 简化ATR
    deviation = (data['close'] - ma20) / (atr + 1e-10)
    # 当处于压缩状态且偏离超过1倍ATR时，预测回归
    signal = np.where(squeeze & (deviation > 1.0), -1.0,
                      np.where(squeeze & (deviation < -1.0), 1.0, 0.0))
    return pd.Series(signal, index=data.index)
