"""AI因子: 动量一致性因子 | 置信:70% | 计算短期动量（过去5日收益）与长期动量（过去20日收益）之间的滚动相关系数（过去10个周期）。若两者正相关且数值高，表明趋势一致，因子接近+1（向上）或-1（向下）；若相关性低或负，表明趋势混乱（未知状态），因子接近0。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class Momentum_Coherence_Factor(BaseFactor):
    """计算短期动量（过去5日收益）与长期动量（过去20日收益）之间的滚动相关系数（过去10个周期）。若两者正相关且数值高，表明趋势一致，因子接近+1（向上）或-1（向下）；若相关性低或负，表明趋势混乱（未知状态），因子接近0。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_momentum_coherence",
            name="Momentum Coherence Factor",
            display_name="动量一致性因子",
            description="计算短期动量（过去5日收益）与长期动量（过去20日收益）之间的滚动相关系数（过去10个周期）。若两者正相关且数值高，表明趋势一致，因子接近+1（向上）或-1（向下）；若相关性低或负，表明趋势混乱（未知状态），因子接近0。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import numpy as np
        close = data['close']
        short_mom = close.pct_change(5)
        long_mom = close.pct_change(20)
        # 计算滚动相关系数，窗口10
        def rolling_corr(x, y):
            return x.rolling(10).corr(y)
        corr = rolling_corr(short_mom, long_mom)
        # 用最近5日方向乘以相关系数得到带符号的动量一致性
        direction = np.sign(short_mom)
        result = corr * direction
        return result.fillna(0).clip(-1, 1)
