"""AI因子: 多周期动量一致性 | 置信:60% | 检测短（5）、中（20）、长（60）周期价格变化方向一致性。当三个周期方向不一致（如短多中空长空）时，容易导致持仓超时或止损，返回负值；完全一致时正值。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumConsistency(BaseFactor):
    """检测短（5）、中（20）、长（60）周期价格变化方向一致性。当三个周期方向不一致（如短多中空长空）时，容易导致持仓超时或止损，返回负值；完全一致时正值。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_consistency",
            name="Multi_Timeframe_Momentum_Consistency",
            display_name="多周期动量一致性",
            description="检测短（5）、中（20）、长（60）周期价格变化方向一致性。当三个周期方向不一致（如短多中空长空）时，容易导致持仓超时或止损，返回负值；完全一致时正值。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        import pandas as pd
        close = data['close']
        # 计算三周期收益率
        ret5 = close.pct_change(5)
        ret20 = close.pct_change(20)
        ret60 = close.pct_change(60)
        # 符号：1为正，-1为负，0为平（用阈值去噪）
        def sign(x, thresh=0.005):
            return np.where(x > thresh, 1, np.where(x < -thresh, -1, 0))
        s5 = sign(ret5)
        s20 = sign(ret20)
        s60 = sign(ret60)
        # 一致性得分：三个符号乘积，若全1得1，全-1得-1，混合则接近0
        product = s5 * s20 * s60
        # 进一步考虑幅度：如果符号一致但幅度很小，则降低置信度
        avg_ret = (np.abs(ret5) + np.abs(ret20) + np.abs(ret60)) / 3.0
        magnitude_factor = np.tanh(avg_ret * 50.0)  # 0~1
        result = product * magnitude_factor
        return pd.Series(result, index=data.index)
