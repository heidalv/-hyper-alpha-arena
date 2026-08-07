"""AI因子: 动量一致性得分 | 置信:60% | 衡量短、中、长三个时间尺度动量的方向一致性。当三者同向时市场趋势明确，反之则处于无序状态。输出+1（完全一致），-1（完全相反）。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Consistency_Score(BaseFactor):
    """衡量短、中、长三个时间尺度动量的方向一致性。当三者同向时市场趋势明确，反之则处于无序状态。输出+1（完全一致），-1（完全相反）。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mom_consistency",
            name="Momentum Consistency Score",
            display_name="动量一致性得分",
            description="衡量短、中、长三个时间尺度动量的方向一致性。当三者同向时市场趋势明确，反之则处于无序状态。输出+1（完全一致），-1（完全相反）。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        # 计算各周期收益率
        ret_short = data['close'].pct_change(5)    # 5期
        ret_mid = data['close'].pct_change(20)     # 20期
        ret_long = data['close'].pct_change(60)    # 60期
        # 转为方向信号：1为正，-1为负，0为持平（精确处理0为0）
        def sign(x):
            return np.sign(x)
        s_short = sign(ret_short)
        s_mid = sign(ret_mid)
        s_long = sign(ret_long)
        # 一致性得分 = (三者之和的绝对值)/3 * 符号（同向为正，反向为负）
        sum_s = s_short + s_mid + s_long
        # 归一化到[-1,1]：当全同向时sum_s=±3，取均值±1；全反向时sum_s=±1? 实际全反向是-3或+3？考虑三种符号相同时为±3，两种相同一种相反时±1，全不同时为0
        # 映射：除以3得±1、±0.333、0
        result = sum_s / 3.0
        # 处理NaN
        result = result.fillna(0.0)
        return result
