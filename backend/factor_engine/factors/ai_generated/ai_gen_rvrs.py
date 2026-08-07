"""AI因子: 反转强度因子 | 置信:60% | 通过计算价格变化与成交量的异常比，识别市场潜在反转点。当价格快速变动但成交量异常放大时，暗示趋势衰竭，可能反转。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class ReversalIntensity(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_rvrs", name="ReversalIntensity",
        display_name="反转强度因子", description="通过计算价格变化与成交量的异常比，识别市场潜在反转点。当价格快速变动但成交量异常放大时，暗示趋势衰竭，可能反转。",
        category="technical", subcategory="mean_reversion",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    volume = data['volume']
    # 价格变化率
    ret = close.pct_change()
    # 成交量相对均值变化
    vol_ma = volume.rolling(20).mean()
    vol_std = volume.rolling(20).std()
    vol_z = (volume - vol_ma) / (vol_std + 1e-10)
    # 结合方向：上涨时放量异常为空头信号，下跌时放量异常为多头信号
    raw = -ret * vol_z
    # 平滑并归一化
    smooth = raw.rolling(5).mean()
    norm = smooth / (smooth.abs().rolling(20).mean() + 1e-10)
    return norm.clip(-1, 1)
