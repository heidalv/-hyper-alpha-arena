"""AI因子: 持有时间风险指标 | 置信:60% | 衡量当前价格相对于近期区间的极端程度与波动率的关系，用于识别持仓时间过长导致的不利波动。利用布林带宽度和价格在带内的位置，当价格脱离中轨且布林带扩张时，风险上升。输出[-1,1]，正值表示高风险区域（应避免持仓），负值表示低风险。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Holding_Time_Risk_Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_holdrisk", name="Holding_Time_Risk_Indicator",
        display_name="持有时间风险指标", description="衡量当前价格相对于近期区间的极端程度与波动率的关系，用于识别持仓时间过长导致的不利波动。利用布林带宽度和价格在带内的位置，当价格脱离中轨且布林带扩张时，风险上升。输出[-1,1]，正值表示高风险区域（应避免持仓），负值表示低风险。",
        category="technical", subcategory="volatility",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    import numpy as np
    close = data['close']
    high = data['high']
    low = data['low']
    # 典型价格
    tp = (high + low + close) / 3
    # 20周期布林带
    ma = tp.rolling(20).mean()
    std = tp.rolling(20).std().fillna(0)
    upper = ma + 2 * std
    lower = ma - 2 * std
    # 价格在布林带内的位置 [0,1]
    band_width = (upper - lower).replace(0, 1e-10)
    position = (close - lower) / band_width
    # 布林带宽度变化率
    bw_change = (band_width - band_width.shift(5)) / band_width.shift(5).replace(0, 1e-10)
    # 风险信号: 远离中轨(0.5)且带宽扩张 => 高风险
    risk = np.abs(position - 0.5) * 2  # [0,1]
    risk = risk * np.clip(bw_change.fillna(0), 0, 1)  # 仅带宽扩张时
    # 转化为[-1,1]: 高风险正，低风险负
    result = (risk - 0.5) * 2
    result = result.fillna(0).clip(-1, 1)
    return result
