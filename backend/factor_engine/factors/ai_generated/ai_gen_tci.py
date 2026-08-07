"""AI因子: 趋势一致性指标 | 置信:60% | 基于短期(5)、中期(20)、长期(60)均线方向一致性，衡量当前趋势的明确程度。当三线同向时输出+1（强趋势），反向或混乱时输出接近-1（未知状态），用于避免在regime=unknown时开仓。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Trend Consistency Indicator(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_tci", name="Trend Consistency Indicator",
        display_name="趋势一致性指标", description="基于短期(5)、中期(20)、长期(60)均线方向一致性，衡量当前趋势的明确程度。当三线同向时输出+1（强趋势），反向或混乱时输出接近-1（未知状态），用于避免在regime=unknown时开仓。",
        category="technical", subcategory="trend",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    # 计算每根均线的方向（1为上升，-1为下降，0为平）
    dir5 = np.sign(ma5.diff())
    dir20 = np.sign(ma20.diff())
    dir60 = np.sign(ma60.diff())
    # 方向一致性得分：三个方向之和除以3，范围[-1,1]
    consistency = (dir5 + dir20 + dir60) / 3.0
    # 填充前60个NaN为0（未知状态）
    consistency = consistency.fillna(0.0)
    return consistency
