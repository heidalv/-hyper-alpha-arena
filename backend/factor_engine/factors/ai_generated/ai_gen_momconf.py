"""AI因子: 动量一致性 | 置信:60% | 衡量短、中、长期动量方向的一致性。当三个时间框架（5、20、60周期）的收益率符号一致时产生强信号，否则减弱。用于过滤假突破和不确定行情。"""
import pandas as pd; import numpy as np
from ..factor_base import BaseFactor, FactorMetadata

class Momentum_Consistency(BaseFactor):
    def get_metadata(self): return FactorMetadata(
        factor_id="ai_gen_momconf", name="Momentum_Consistency",
        display_name="动量一致性", description="衡量短、中、长期动量方向的一致性。当三个时间框架（5、20、60周期）的收益率符号一致时产生强信号，否则减弱。用于过滤假突破和不确定行情。",
        category="composite", subcategory="momentum",
        version="1.0.0-ai", author="AI Generated (D7)")

    def calculate(self, data):
    close = data['close']
    ret5 = close.pct_change(5)
    ret20 = close.pct_change(20)
    ret60 = close.pct_change(60)
    # 符号一致性：三个同号则强度为符号乘积取平均
    sign5 = np.sign(ret5)
    sign20 = np.sign(ret20)
    sign60 = np.sign(ret60)
    # 一致性分数：三个符号之和的绝对值除以3，再乘以平均符号
    avg_sign = (sign5 + sign20 + sign60) / 3.0
    consistency = avg_sign * np.abs(avg_sign)  # 使范围更线性
    result = np.clip(consistency, -1, 1)
    return result.fillna(0.0)
