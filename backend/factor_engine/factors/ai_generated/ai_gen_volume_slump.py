"""AI因子: 成交量枯竭反转因子 | 置信:60% | 识别成交量极度萎缩时价格接近极端水平可能引发的反转。当成交量低于过去20日均值的50%且价格处于过去20日最高价的95%分位以上时，看空（-1）；当成交量低于均值的50%且价格处于过去20日最低价的5%分位以下时，看多（+1）。信号强度根据偏离程度线性映射。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume Drought Reversal Factor(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_volume_slump", name="Volume Drought Reversal Factor",
        display_name="成交量枯竭反转因子", description="识别成交量极度萎缩时价格接近极端水平可能引发的反转。当成交量低于过去20日均值的50%且价格处于过去20日最高价的95%分位以上时，看空（-1）；当成交量低于均值的50%且价格处于过去20日最低价的5%分位以下时，看多（+1）。信号强度根据偏离程度线性映射。",
        category="technical", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    high = data['high']
    low = data['low']
    volume = data['volume']
    # 过去20日均成交量
    avg_vol_20 = volume.rolling(20).mean()
    vol_ratio = volume / avg_vol_20.replace(0, np.nan)
    # 成交量萎缩条件：低于50%
    slump = vol_ratio < 0.5
    # 价格极端位置：使用最高价和最低价的20日百分位
    high_20 = high.rolling(20).max()
    low_20 = low.rolling(20).min()
    # 使用收盘价相对于20日区间的位置
    price_range = high_20 - low_20
    position = (close - low_20) / price_range.replace(0, np.nan)
    # 高价区：95%以上；低价区：5%以下
    high_zone = position > 0.95
    low_zone = position < 0.05
    # 信号
    signal = pd.Series(0, index=data.index)
    signal[slump & high_zone] = -1.0
    signal[slump & low_zone] = 1.0
    return signal
