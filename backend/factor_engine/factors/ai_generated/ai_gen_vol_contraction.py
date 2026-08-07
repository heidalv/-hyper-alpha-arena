"""AI因子: 成交量收缩挤压因子 | 置信:60% | 衡量成交量的收缩程度与价格窄幅波动的组合，识别市场即将出现方向性选择的时刻。历史亏损模式中多次在小成交量下出现假突破止损。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume Contraction Squeeze(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_contraction", name="Volume Contraction Squeeze",
        display_name="成交量收缩挤压因子", description="衡量成交量的收缩程度与价格窄幅波动的组合，识别市场即将出现方向性选择的时刻。历史亏损模式中多次在小成交量下出现假突破止损。",
        category="behavioral", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    
    volume = data['volume']
    close = data['close']
    high = data['high']
    low = data['low']
    
    # 计算成交量相对均值收缩
    vol_ma20 = volume.rolling(20).mean()
    vol_ma50 = volume.rolling(50).mean()
    vol_ratio = vol_ma20 / (vol_ma50 + 1e-10)
    
    # 价格波动幅度（ATR相对价格百分比）
    atr_period = 10
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()
    atr_pct = atr / (close + 1e-10) * 100
    
    # 价格窄幅：ATR百分比处于低分位
    atr_percentile = atr_pct.rolling(100).rank(pct=True)
    narrow = atr_percentile < 0.2
    
    # 成交量收缩：vol_ratio<0.8且处于低分位
    vol_percentile = vol_ratio.rolling(100).rank(pct=True)
    contraction = (vol_ratio < 0.85) & (vol_percentile < 0.3)
    
    # 挤压信号：窄幅+量缩
    squeeze = narrow & contraction
    
    # 挤压后可能的突破方向不确定，但结合近期价格形态：若价格在区间内，则上下概率均衡？
    # 我们可以根据价格在区间内的位置来赋予倾向：若靠近区间上沿，则向下突破概率大（-1）；
    # 若靠近下沿，则向上突破概率大（+1）。
    # 计算过去20日价格区间
    high_max = high.rolling(20).max()
    low_min = low.rolling(20).min()
    price_range = high_max - low_min
    position = (close - low_min) / (price_range + 1e-10)
    
    # 靠近上沿（>0.8）则看空，靠近下沿（<0.2）则看多
    factor = pd.Series(0, index=data.index)
    factor[squeeze & (position > 0.8)] = -1.0
    factor[squeeze & (position < 0.2)] = 1.0
    # 中间区域给中性
    
    # 平滑
    result = factor.rolling(3).mean().fillna(0)
    return result
