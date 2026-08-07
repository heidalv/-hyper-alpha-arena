"""AI因子: 多时间动量一致性 | 置信:70% | 计算短、中、长三个时间尺度（5日、20日、60日）的动量方向。若三个方向不一致（尤其短期与长期背离），则市场状态不明，做多风险高。输出三个动量的符号乘积，方向一致为正，不一致为负。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Time_Momentum_Consistency(BaseFactor):
    """计算短、中、长三个时间尺度（5日、20日、60日）的动量方向。若三个方向不一致（尤其短期与长期背离），则市场状态不明，做多风险高。输出三个动量的符号乘积，方向一致为正，不一致为负。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_multi_time_momentum",
            name="Multi-Time Momentum Consistency",
            display_name="多时间动量一致性",
            description="计算短、中、长三个时间尺度（5日、20日、60日）的动量方向。若三个方向不一致（尤其短期与长期背离），则市场状态不明，做多风险高。输出三个动量的符号乘积，方向一致为正，不一致为负。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        # 三个时间尺度动量
        mom5 = close.pct_change(5)
        mom20 = close.pct_change(20)
        mom60 = close.pct_change(60)
        # 符号：1表示正，-1表示负
        sign5 = np.sign(mom5)
        sign20 = np.sign(mom20)
        sign60 = np.sign(mom60)
        # 一致性：三个符号的乘积，+1表示全部一致，-1表示至少一个不一致
        consistency = sign5 * sign20 * sign60
        # 使用动量强度加权，使数值在[-1,1]连续
        avg_mom = (mom5.abs() + mom20.abs() + mom60.abs()) / 3
        weighted = consistency * np.tanh(avg_mom * 10)
        return weighted.fillna(0)
