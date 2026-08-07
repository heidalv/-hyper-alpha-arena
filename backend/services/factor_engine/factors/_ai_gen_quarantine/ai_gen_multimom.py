"""AI因子: 多周期动量一致性 | 置信:60% | 计算短期（5日）和中期（20日）动量方向的一致性。若两者同向则得分接近+1，反向则接近-1，模糊则接近0。经验表明在动量不一致时趋势策略容易亏损（出现反转或震荡）。用相关系数或符号一致率。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_Timeframe_Momentum_Consistency(BaseFactor):
    """计算短期（5日）和中期（20日）动量方向的一致性。若两者同向则得分接近+1，反向则接近-1，模糊则接近0。经验表明在动量不一致时趋势策略容易亏损（出现反转或震荡）。用相关系数或符号一致率。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_multimom",
            name="Multi-Timeframe Momentum Consistency",
            display_name="多周期动量一致性",
            description="计算短期（5日）和中期（20日）动量方向的一致性。若两者同向则得分接近+1，反向则接近-1，模糊则接近0。经验表明在动量不一致时趋势策略容易亏损（出现反转或震荡）。用相关系数或符号一致率。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        # 短期动量：5日百分比变化
        mom_short = close.pct_change(5)
        # 中期动量：20日百分比变化
        mom_med = close.pct_change(20)
        # 归一化到[-1,1]：取符号乘积或相关性
        # 使用符号一致性：同号得1，异号得-1，任一为0得0
        sign_short = np.sign(mom_short)
        sign_med = np.sign(mom_med)
        # 当两者都为0时设为0
        consistency = sign_short * sign_med
        # 用指数加权平滑避免毛刺
        result = consistency.ewm(span=3).mean()
        # 如果动量本身非常小（接近0），则视为无方向，设为0
        threshold = 0.001
        mask = (np.abs(mom_short) < threshold) | (np.abs(mom_med) < threshold)
        result[mask] = 0.0
        return result.fillna(0.0)
