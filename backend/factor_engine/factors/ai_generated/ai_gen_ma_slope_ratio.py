"""AI因子: 均线斜率比因子 | 置信:60% | 计算短期（5日）与长期（20日）移动平均线斜率（即当前值相对N日前的变化率）之比，用于识别趋势强度突变。当比值从高位回落时可能趋势衰竭，产生反转信号。返回[-1,+1]，正值表示短期斜率强于长期（趋势延续），负值表示短期弱于长期（趋势减弱）。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class MA Slope Ratio(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_ma_slope_ratio", name="MA Slope Ratio",
        display_name="均线斜率比因子", description="计算短期（5日）与长期（20日）移动平均线斜率（即当前值相对N日前的变化率）之比，用于识别趋势强度突变。当比值从高位回落时可能趋势衰竭，产生反转信号。返回[-1,+1]，正值表示短期斜率强于长期（趋势延续），负值表示短期弱于长期（趋势减弱）。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import pandas as pd
    import numpy as np
    close = data['close']
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    # 斜率：当前值相对5天前的变化率
    slope5 = (ma5 - ma5.shift(5)) / ma5.shift(5)
    slope20 = (ma20 - ma20.shift(5)) / ma20.shift(5)
    # 避免除零
    ratio = slope5 / (slope20.abs() + 1e-10)
    # 使用符号并限幅
    result = np.clip(ratio, -1, 1)
    return result
