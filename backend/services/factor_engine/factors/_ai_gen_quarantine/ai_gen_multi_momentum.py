"""AI因子: 多周期动量一致性 | 置信:60% | 计算短(3日)、中(10日)、长(30日)三个时间周期的收益率符号，若三周期同向则赋予较强信号，否则信号衰减。输出[-1,1]：+1表示全部向上且强度大，-1表示全部向下且强度大，0表示方向分歧。用于过滤多空分歧较大的震荡行情。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Multi_timeframe_Momentum_Consistency(BaseFactor):
    """计算短(3日)、中(10日)、长(30日)三个时间周期的收益率符号，若三周期同向则赋予较强信号，否则信号衰减。输出[-1,1]：+1表示全部向上且强度大，-1表示全部向下且强度大，0表示方向分歧。用于过滤多空分歧较大的震荡行情。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_multi_momentum",
            name="Multi-timeframe Momentum Consistency",
            display_name="多周期动量一致性",
            description="计算短(3日)、中(10日)、长(30日)三个时间周期的收益率符号，若三周期同向则赋予较强信号，否则信号衰减。输出[-1,1]：+1表示全部向上且强度大，-1表示全部向下且强度大，0表示方向分歧。用于过滤多空分歧较大的震荡行情。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data: pd.DataFrame) -> pd.Series:
        import pandas as pd
        import numpy as np
        close = data['close']
        ret3 = close.pct_change(3)
        ret10 = close.pct_change(10)
        ret30 = close.pct_change(30)
        avg_ret = (ret3 + ret10 + ret30) / 3.0
        sign3 = np.sign(ret3)
        sign10 = np.sign(ret10)
        sign30 = np.sign(ret30)
        consistency = (sign3 + sign10 + sign30).abs() - 1  # 一致时=2,分歧时=0或1
        # 方向性强度：平均收益率标准化
        strength = avg_ret / (avg_ret.abs().rolling(50).mean() + 1e-8)  # 近似z-score
        strength = strength.clip(-2, 2) / 2.0  # 压缩到[-1,1]
        # 最终信号：一致性权重 * 强度
        result = (consistency / 2.0) * strength
        result = result.clip(-1, 1)
        return result.fillna(0.0)
