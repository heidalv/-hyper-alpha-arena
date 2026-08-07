"""AI因子: 尾部动量因子 | 置信:60% | 捕捉极端价格变动后的反向动量。通过比较当前价格与过去N日布林带位置，并加入成交量的确认，识别超买超卖后的回归。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class TailMomentum(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_tail", name="TailMomentum",
        display_name="尾部动量因子", description="捕捉极端价格变动后的反向动量。通过比较当前价格与过去N日布林带位置，并加入成交量的确认，识别超买超卖后的回归。",
        category="technical", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    volume = data['volume']
    # 布林带
    ma = close.rolling(20).mean()
    std = close.rolling(20).std()
    z = (close - ma) / (std + 1e-10)
    # 成交量确认：极端价格伴随高成交量加强反转信号
    vol_ma = volume.rolling(20).mean()
    vol_ratio = volume / (vol_ma + 1e-10)
    # 组合：z正且大 -> 空头（-1），z负且大 -> 多头（+1）
    # 使用tanh使输出平滑映射到[-1,1]
    raw = -np.tanh(z * 0.5) * np.clip(vol_ratio, 0.5, 2.0)
    # 平滑
    smooth = raw.rolling(3).mean()
    # 最终归一化到[-1,1]
    result = smooth.clip(-1, 1)
    return result
