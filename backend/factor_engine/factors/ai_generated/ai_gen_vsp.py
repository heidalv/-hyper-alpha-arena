"""AI因子: 成交量异常与价格方向 | 置信:60% | 结合成交量激增与价格变化方向，识别假突破或异常资金行为。若价格突破时成交量远高于近期均值，但随后未能持续，可能为陷阱。因子计算当前成交量与过去20期均值之比，乘以价格变化符号（涨为正，跌为负），再通过tanh归一化。极端正值为放量上涨，可能过热；极端负值为放量下跌，可能恐慌。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume Spike with Price Direction(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vsp", name="Volume Spike with Price Direction",
        display_name="成交量异常与价格方向", description="结合成交量激增与价格变化方向，识别假突破或异常资金行为。若价格突破时成交量远高于近期均值，但随后未能持续，可能为陷阱。因子计算当前成交量与过去20期均值之比，乘以价格变化符号（涨为正，跌为负），再通过tanh归一化。极端正值为放量上涨，可能过热；极端负值为放量下跌，可能恐慌。",
        category="volume", subcategory="volume",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    volume = data['volume']
    vol_ma = volume.rolling(20).mean()
    vol_ratio = volume / (vol_ma + 1e-6)
    price_change = close.pct_change()
    # 假设放量上涨为正向，放量下跌为负向
    raw = vol_ratio * price_change
    # 用tanh压缩到[-1,1]，乘以2放大敏感度
    result = np.tanh(raw * 2)
    return result.fillna(0).clip(-1, 1)
