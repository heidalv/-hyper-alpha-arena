"""AI因子: 多时间框架动量一致性 | 置信:60% | 分别计算短期(5期)和中期(20期)的动量方向（价格变化百分比）。两者同号时输出正值（幅度为平均动量的归一化），异号时输出负值。旨在捕捉趋势分歧导致的反转风险，与亏损模式中的max_hold_timeout和master_running相关。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Momentum_Consistency(BaseFactor):
    """分别计算短期(5期)和中期(20期)的动量方向（价格变化百分比）。两者同号时输出正值（幅度为平均动量的归一化），异号时输出负值。旨在捕捉趋势分歧导致的反转风险，与亏损模式中的max_hold_timeout和master_running相关。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mtmc",
            name="Multi-Timeframe Momentum Consistency",
            display_name="多时间框架动量一致性",
            description="分别计算短期(5期)和中期(20期)的动量方向（价格变化百分比）。两者同号时输出正值（幅度为平均动量的归一化），异号时输出负值。旨在捕捉趋势分歧导致的反转风险，与亏损模式中的max_hold_timeout和master_running相关。",
            category="technical",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        close = data['close']
        mom_short = close.pct_change(5)
        mom_med = close.pct_change(20)
        # 符号一致性
        sign_short = np.sign(mom_short)
        sign_med = np.sign(mom_med)
        consistency = (sign_short == sign_med).astype(float)
        # 方向一致时赋予平均动量强度，方向相反时赋予负强度
        avg_mom = (mom_short + mom_med) / 2
        result = consistency * avg_mom - (1 - consistency) * avg_mom.abs()
        # 用tanh归一化到[-1,1]
        result = np.tanh(result * 10)  # 放大后压缩
        return result
