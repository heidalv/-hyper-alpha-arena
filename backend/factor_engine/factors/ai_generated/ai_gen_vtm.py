"""AI因子: 成交量时间动量 | 置信:55% | 结合成交量异常与价格偏离移动均线的程度，识别持仓超时或止损风险。当成交量异常放大且价格远离均值时给出负信号"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volume Time Momentum(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vtm", name="Volume Time Momentum",
        display_name="成交量时间动量", description="结合成交量异常与价格偏离移动均线的程度，识别持仓超时或止损风险。当成交量异常放大且价格远离均值时给出负信号",
        category="composite", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    volume = data['volume']
    # 价格偏离20日均线
    ma20 = close.rolling(20).mean()
    deviation = (close - ma20) / ma20
    # 成交量相对20日均值
    vol_ratio = volume / volume.rolling(20).mean()
    # 短期波动率（用价格变化率）
    ret1 = close.pct_change()
    # 当偏离大且放量且波动率异常时负信号
    signal = -deviation.abs() * (vol_ratio - 1).clip(0) * ret1.abs()
    result = signal / (signal.abs().mean() + 1e-8)
    return result.clip(-1, 1)
