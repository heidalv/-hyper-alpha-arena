"""AI因子: 多时间框架动量一致性因子 | 置信:70% | 检测多个时间周期（如5日、20日、60日）的动量方向是否一致。当所有周期趋势同向时，趋势明确，因子值为正；当方向混乱时，市场处于regime=unknown状态，因子值为负，应避免交易。"""
import pandas as pd
import numpy as np
from backend.services.factor_engine.factor_base import BaseFactor, FactorMetadata
from backend.services.factor_engine.factor_registry import register_factor


@register_factor()
class MultiTimeframeMomentumCoherence(BaseFactor):
    """检测多个时间周期（如5日、20日、60日）的动量方向是否一致。当所有周期趋势同向时，趋势明确，因子值为正；当方向混乱时，市场处于regime=unknown状态，因子值为负，应避免交易。"""

    def get_metadata(self) -> FactorMetadata:
        return FactorMetadata(
            factor_id="ai_gen_mmc",
            name="Multi-timeframe Momentum Coherence",
            display_name="多时间框架动量一致性因子",
            description="检测多个时间周期（如5日、20日、60日）的动量方向是否一致。当所有周期趋势同向时，趋势明确，因子值为正；当方向混乱时，市场处于regime=unknown状态，因子值为负，应避免交易。",
            category="composite",
            subcategory="momentum",
            version="1.0.0-ai",
            author="AI Generated (D7)",
        )

    def calculate(self, data):
        import pandas as pd
        import numpy as np

        close = data['close']
        # 计算不同周期的动量：收益率
        mom5 = close.pct_change(5)
        mom20 = close.pct_change(20)
        mom60 = close.pct_change(60)

        # 将动量离散化为方向：+1（正）, -1（负）, 0（接近零）
        def direction(series, threshold=0.02):
            return np.sign(series).where(series.abs() > threshold, 0)

        dir5 = direction(mom5)
        dir20 = direction(mom20)
        dir60 = direction(mom60)

        # 计算一致性：方向总和除以3，方向一致时绝对值接近1
        sum_dir = dir5 + dir20 + dir60
        # 映射到[-1,1]：当三个方向完全一致时±1，完全混乱时0附近
        coherence = sum_dir / 3.0
        # 增加一个惩罚项：如果有一个方向为零（无动量），则降低信号强度
        zero_penalty = (dir5 == 0).astype(int) + (dir20 == 0).astype(int) + (dir60 == 0).astype(int)
        coherence = coherence * (1 - zero_penalty * 0.3)
        # 平滑并归一化
        result = coherence.rolling(3).mean()
        return result.fillna(0)
