"""AI因子: 波动率尖峰指标 | 置信:60% | 当短期波动率（最近5根K线）相对于长期波动率（最近50根K线）异常升高时发出负向信号，因为高波动可能预示市场状态不明朗，容易导致策略失效。使用收盘价收益率计算波动率，信号值归一化到[-1,1]。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Spike Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vol_spike", name="Volatility Spike Indicator",
        display_name="波动率尖峰指标", description="当短期波动率（最近5根K线）相对于长期波动率（最近50根K线）异常升高时发出负向信号，因为高波动可能预示市场状态不明朗，容易导致策略失效。使用收盘价收益率计算波动率，信号值归一化到[-1,1]。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    ret = data['close'].pct_change()
    short_vol = ret.rolling(5).std()
    long_vol = ret.rolling(50).std()
    ratio = short_vol / (long_vol + 1e-10)
    # 将ratio映射到[-1,1]，阈值取2作为极端
    signal = (ratio - 1.5) / 0.5  # 1.5为中心，0.5为半宽
    signal = np.clip(signal, -1, 1)
    return signal.fillna(0)
