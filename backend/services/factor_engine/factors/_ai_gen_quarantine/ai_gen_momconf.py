"""AI因子: 多周期动量冲突震荡因子 | 置信:60% | 当短期动量（如3日ROC）与中期动量（如14日ROC）方向相反时，表明市场处于震荡或转折区域，趋势信号混乱，容易导致假突破和止损触发。该因子度量动量冲突程度。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Conflict_Oscillator(BaseFactor):
    """当短期动量（如3日ROC）与中期动量（如14日ROC）方向相反时，表明市场处于震荡或转折区域，趋势信号混乱，容易导致假突破和止损触发。该因子度量动量冲突程度。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momconf",
            name="Momentum Conflict Oscillator",
            display_name="多周期动量冲突震荡因子",
            description="当短期动量（如3日ROC）与中期动量（如14日ROC）方向相反时，表明市场处于震荡或转折区域，趋势信号混乱，容易导致假突破和止损触发。该因子度量动量冲突程度。",
            category="composite",
            subcategory="trend",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np
        # 短期动量：3日收益率
        roc3 = data['close'].pct_change(3)
        # 中期动量：14日收益率
        roc14 = data['close'].pct_change(14)
        # 方向符号
        sign3 = np.sign(roc3)
        sign14 = np.sign(roc14)
        # 冲突：方向不一致
        conflict = (sign3 != sign14).astype(float)
        # 动量强度：取短期与中期动量的绝对值的平均值作为强度
        strength = (roc3.abs() + roc14.abs()) / 2
        # 最终因子：冲突且动量强度大于某个阈值（如0.01）
        factor = conflict * (strength > 0.01).astype(float) * (1 - 2 * (sign3 == 1).astype(float))  # 短期看多时负，看空时正
        # 简化：直接使用冲突乘以符号
        factor = conflict * (-sign3)  # 当短期看多（+1）时输出-1，看空时输出+1，但需要归一化
        # 由于sign3为-1,0,1，冲突时conflict=1，则factor为-1,0,1。归一化到[-1,1]
        return factor.fillna(0).clip(-1, 1)
