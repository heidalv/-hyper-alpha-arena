"""AI因子: 微观噪声指数 | 置信:55% | 基于价格变动与成交量关系计算噪声指数。使用一分钟K线（假设输入为1min）计算价格变化与成交量的相关性，当噪声高（随机波动）时输出0，当有持续性趋势时输出方向。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Microstructure_Noise_Index(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_mic_noi", name="Microstructure_Noise_Index",
        display_name="微观噪声指数", description="基于价格变动与成交量关系计算噪声指数。使用一分钟K线（假设输入为1min）计算价格变化与成交量的相关性，当噪声高（随机波动）时输出0，当有持续性趋势时输出方向。",
        category="behavioral", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    import pandas as pd
    close = data['close']
    volume = data['volume']
    # 价格变动
    ret = close.pct_change()
    # 成交量变化率
    vol_change = volume.pct_change()
    # 滚动相关系数（20期）
    corr = ret.rolling(20).corr(vol_change)
    # 噪声指数：当相关系数接近0时噪声高，接近±1时趋势明确
    # 映射到[-1,1]：使用相关系数本身，但绝对值低于阈值时置0
    noise = corr.fillna(0)
    threshold = 0.3
    result = pd.Series(0.0, index=data.index)
    # 正相关：价格涨伴随量增，趋势看多；负相关：价格涨量缩，可能反转看空
    strong_pos = (noise > threshold)
    strong_neg = (noise < -threshold)
    result[strong_pos] = 1.0
    result[strong_neg] = -1.0
    # 注意：这里方向基于相关系数符号，但实际应考虑价格本身方向？简单处理：正相关且近期上涨为1，下跌-1需进一步
    # 改进：结合价格方向
    trend = close - close.shift(10)
    result[strong_pos & (trend > 0)] = 1.0
    result[strong_pos & (trend < 0)] = -1.0
    result[strong_neg & (trend > 0)] = -1.0  # 量缩上涨，看跌
    result[strong_neg & (trend < 0)] = 1.0   # 量缩下跌，看涨
    return result
