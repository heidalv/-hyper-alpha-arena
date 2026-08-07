"""AI因子: 波动率不稳定性 | 置信:60% | 度量近期波动率相对于长期波动率的变化，反映市场状态突变风险。正值表示波动率上升（可能趋势启动或反转），负值表示波动率下降（可能进入震荡）。用于识别regime unknown的潜在区域。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Volatility Instability(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_vi", name="Volatility Instability",
        display_name="波动率不稳定性", description="度量近期波动率相对于长期波动率的变化，反映市场状态突变风险。正值表示波动率上升（可能趋势启动或反转），负值表示波动率下降（可能进入震荡）。用于识别regime unknown的潜在区域。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    log_ret = np.log(close / close.shift(1))
    short_vol = log_ret.rolling(20).std() * np.sqrt(20)  # 年化？仅相对值
    long_vol = log_ret.rolling(60).std() * np.sqrt(60)
    ratio = short_vol / long_vol.replace(0, np.nan) - 1
    ratio = ratio.fillna(0)
    factor = np.clip(ratio, -2, 2) / 2.0
    return factor
